# invoice-ocr/ml — 확정 구조 & 마이그레이션 인터페이스 (2026-06-26)

- 작성일: 2026-06-26
- 성격: **마이그레이션 설계 참조용 확정 구조 문서**. macmini 이전([`2026-06-24-macmini-migration-overview.md`](./2026-06-24-macmini-migration-overview.md)) §6 `apps/invoice-ocr/ml/` 모듈이 **무엇을 노출하고(통합 표면), 무엇이 production·spike·미구현인지**를 정리해 backend↔ml·worker↔ml 연결을 설계 가능하게 한다.
- 관련 정본: spec [`2026-06-25-sp2-handwriting-recognizer-findings.md`](./2026-06-25-sp2-handwriting-recognizer-findings.md), 진행 핸드오프 [`../2026-06-26-sp2-grouping-progress.md`](../2026-06-26-sp2-grouping-progress.md), 모듈 가이드 `apps/invoice-ocr/ml/CLAUDE.md`.
- 검증 기준일: 2026-06-26. 모든 수치는 이 날짜 코드/산출물 실측(아래 §7 테스트, §6 인벤토리).

---

## 0. 한 줄 결론 — 토대는 "완성"인가?

**수직 슬라이스(신규 사진 → 검출/워프 → 그룹핑 → 품목 인식 + 금액 인식 + 합계검산)는 처음 보는 사진에서 end-to-end로 작동함이 실측 검증됐다.** 단 아래를 명확히 구분한다:

- ✅ **PoC 토대 완성**: 두 트랙(SP1 숫자 production, SP2 손글씨 spike) 모두 동작. 인식기 선택 확정(품목=ddobokki contrastive, 금액=Qwen3-VL-8B). 신규·기울어진 사진 2장에 운영 경로 그대로 통과.
- ⚠️ **production-hardening은 미완**: SP2 코드는 **gitignore된 spike**(production 테스트·불변성 규약 미적용). 본선 통합은 `RecognizerAdapter` 뒤로 올려야 함(findings §8-8).
- ❌ **미구현/보류**: §6 금액합 자동검산(`group_amounts.py`), 재워프 회수 18장, 합계행 자동검출 — §8 참조.

→ 마이그레이션은 **"검증된 PoC를 production 서비스로 승격 + 피드백 루프 자동화"** 단계로 진입하면 된다. 모델·알고리즘 탐색은 끝났다.

---

## 1. 두 트랙 구조

| 트랙 | 위치 | git | 테스트 | 역할 | 모델 |
|---|---|---|---|---|---|
| **SP1 본 파이프라인** | `ocr_poc/` | tracked | **53 passed** | 숫자 셀 OCR 수직 슬라이스(검출→열매핑→인식→정규화→검산→채점) | PaddleOCR 3.x (어댑터 뒤) |
| **SP2 스파이크** | `report/sp2_spike/` | **gitignore** | **27 passed**(순수코어만) | 손글씨 인식 — 품목 작성자-특화 retrieval + 금액 VLM OCR + 그룹핑 | ddobokki contrastive · Qwen3-VL-8B |

- **SP1은 production 규약**(전부 `@dataclass(frozen=True)`, 순수함수 계층, Fake 어댑터로 결정론 테스트, 코어는 paddle-free 경량).
- **SP2는 실험물**: 알고리즘·모델을 빠르게 탐색한 로컬 전용 코드. 본선 승격 시 SP1의 어댑터 인터페이스 뒤로 통합한다.

---

## 2. 수직 파이프라인 단계 (확정된 처리 흐름)

신규 사진 1장이 거치는 운영 경로 — `report/sp2_spike/item/infer_photo.py`가 이 전체를 구현(이번 세션 검증 산출물).

```
사진(EXIF 정위치 로드)
 → ① form_quad_robust 워프 + deskew              [rectify.py / grid_v4.py]  ✅ robust(기울어진 사진도 정류)
 → ② φ-그리드 행검출(고정피치 P + offset)          [rows.py / canon.py]       ✅ 68/74 입증, HEADER skip
 → ③ 이중신호 그룹핑(item/amt ink → new/cont/empty) [group.py(순수)]           ✅ 27 테스트, ⚠️ 단가숫자 품목칸 침범 엣지케이스
 → ④ 품목 crop(ink-snap) → 임베딩 → 뱅크 retrieval   [train_contrastive.py]     ◐ 재현 어휘만(신규는 타이핑)
 → ⑤ 금액칸 Qwen3-VL 전사 → 정수 파싱                [infer_photo.py]           ✅ 8/9 실측, 합계 검산
 → ⑥ (미구현) 금액합 == 블록 정합 검산              [group_amounts.py 없음]    ❌
```

핵심 알고리즘 결정(상세는 진행 핸드오프 §3):
- **이중신호 분류**: `amt_ink<임계`→empty, 있고 `item_ink≥임계`→new, 아니면 cont(약식분해 합산). 빈 합산칸 오인식 차단.
- **trim_to_data_block**: 첫 데이터행~첫 빈행 연속블록만 유지(하단 합계·전화·메모 배제).
- **ink-snap 박스**: 밴드 내 stroke 첫/끝 행에 세로 스냅(+PAD). 글자 없는 곳 시작 결함 해결.
- **워프 좌표(grid_v4)**: `ITEM_X=(100,392)` · `AMOUNT_X=(612,896)` · `DATA_Y=(612,1948)` · WARP 900×2100. 전역 피치 `P≈81`.

---

## 3. 인식기 — 확정 선택과 성능 (마이그레이션이 서빙할 모델)

| 대상 | 채택 모델 | 방식 | 성능(실측) | 비고 |
|---|---|---|---|---|
| **품목명** | `ddobokki/ko-trocr` 인코더 + contrastive head | 작성자-특화 **개방 retrieval**(뱅크 대조) | 4-fold CV **top-1 59.8 / top-3 72.8 / top-5 76.6%** (베이스 49.5%) | 재현 어휘만. 신규(첫 등장) ~32%는 retrieval 불가→타이핑 |
| **금액(공급대가)** | `mlx-community/Qwen3-VL-8B-Instruct-4bit` | 손글씨 VLM per-cell 전사 | findings 공급가열 **84.8%** · 신규 2장 **8/9(89%)** | SP1 stock PP-OCRv5(~10%) 폐기한 자리. 합계검산이 오류탐지 |

- **인식기 천장 = 인코더 도메인**(findings §183): "한글이면 다 좋다"가 아니라 **OCR 도메인 한글 인코더**여야 함. crop 품질 개선엔 top-1 평탄.
- **품목은 무인 자동화 아님 — HITL 입력보조**: 재현 행 타이핑을 ~60% 줄이는 pre-fill + top-5 드롭다운. DB가 쌓일수록 신규→재현 전환으로 자동 개선(작성자 특화 학습의 핵심).
- **금액은 어휘 무관**(일반 손글씨 OCR)이라 신규 전표에도 즉시 작동.

---

## 4. 통합 표면 — 마이그레이션이 연결할 인터페이스

### 4.1 어댑터 Protocol (본선 통합 계약) — `ocr_poc/`

```python
# ocr_poc/detect.py
class DetectorAdapter(Protocol):
    def detect(self, image_path: str) -> list[DetectedCell]: ...
# 구현: FakeDetector(테스트) / TextDetCellDetector(실모델)

# ocr_poc/recognize.py
class RecognizerAdapter(Protocol):
    def recognize(self, crop) -> str: ...
# 구현: FakeRecognizer(테스트) / PaddleOCRNumeric · ReferenceOCR(실모델)
```

→ **SP2 손글씨 인식기(품목 retrieval·금액 VLM)는 이 `RecognizerAdapter` 뒤로 올려 본선에 plug-in한다**(findings §8-8). 실모델 어댑터는 **엔진 지연 로딩**(첫 `recognize()` 호출 시 import/init) — 테스트가 무거운 의존 없이 돈다.

### 4.2 추론 엔트리 (운영 경로) — `report/sp2_spike/item/infer_photo.py`

신규 사진 → 품목+금액 추출의 **de-facto 추론 계약**. 마이그레이션의 ML 서비스가 감쌀 함수.

- 입력: 사진 경로(들)
- 처리: `process_one()` = 워프→그룹핑→품목 retrieval(`bank.npz`)+금액 OCR
- 출력(행별): `(crop, top-5 품목라벨+유사도, 금액 정수, 금액칸 원문)` + 합계
- 산출물: `review/infer_photo.html`(검수용 시각화)

### 4.3 학습 엔트리 (재학습 잡) — `report/sp2_spike/item/train_contrastive.py`

- `--production` → 전체 clean crop 학습(22 epoch 고정) → **`runs/ft_prod.pt` + `runs/bank.npz`**(배포 모델+뱅크)
- `--folds 4` → 신뢰 평가(4-fold CV) → `review/finetune_report.json`
- 추론 = 신규 crop 임베딩 → 뱅크 retrieval. **재학습 = 뱅크 갱신**(새 교정 crop 누적).

### 4.4 SP1 CLI 엔트리 — `python -m ocr_poc`

- `match-extract` → references(인쇄 정본) OCR → `results/reviewed_dates.csv`(사람 검수)
- `run` → 검수CSV + DB → 배치 추론 → `report/`
- `run_pipeline()`(`__main__.py`) = 어댑터/이미지오프너 주입받는 **순수 오케스트레이션**(모킹 end-to-end 스모크 가능).

### 4.5 경계 env (`ocr_poc/config.py`) — 절대경로 하드코딩 금지

| env | 용도 | 미설정 시 |
|---|---|---|
| `SJMJ_DATA_DIR` | 데이터 루트(images/labels/references) | RuntimeError |
| `SJMJ_DB_BACKUP` | MySQL 덤프(.sql) 경로 | RuntimeError |

→ 마이그레이션 시 이 두 env가 **backend/worker 컨테이너에 주입**되어야 ML이 데이터·GT에 접근. config가 경계에서 검증.

---

## 5. 피드백 루프 매핑 (migration §5 ↔ 현재 구현)

migration overview §5의 자동화 루프가 현재 코드에서 어떻게 실현되는지:

```
사진 업로드 →[worker 큐]→ ML 추론(§2 파이프라인) → 초안 JSON(품목 top-5 + 금액)
   → 검수 UI(=grouping_editor.html · label_inspect.html 역할) 사람 교정
   → 운영DB 확정 저장
   → 교정 결과가 곧 학습 정답:
        grouping_corrections.json (행타입·db_skip 교정)
        dataset_corrections.json  (라벨 drop/relabel/merge 교정)
   → train_contrastive.py --production 재실행 → ft_prod.pt + bank.npz 갱신 → 정확도 ↑
```

- **GT 자동축적의 토대**: 운영DB가 정답지(manifest 91전표 매칭 확정). 사진↔DB 매칭키 = **`total_supply`(공급가합, VAT 제외)**, `grand_total` 아님(매칭키 틀리면 조회 0건).
- 현재는 교정 JSON + 수동 재학습. 마이그레이션이 이를 **worker 잡 + 임계 트리거**로 자동화하면 §5 루프 완성.

---

## 6. 산출물·데이터 인벤토리 (위치 & 규모)

| 자산 | 경로 | 규모(실측) | git |
|---|---|---|---|
| 배포 모델 | `report/sp2_spike/item/runs/ft_prod.pt` | 348MB | gitignore |
| CV best 체크포인트 | `…/runs/ft_best.pt` | 348MB | gitignore |
| 배포 뱅크 | `…/runs/bank.npz` | **271 crop · 122 라벨 · 67 전표** | gitignore |
| 그룹핑 라벨셋 | `report/dataset_grouped/<DB명>/` | **280 crop · 135 라벨폴더** | gitignore |
| 매칭 manifest(GT) | `data/image_dataset/manifest.json` | **91 전표** | gitignore |
| 교정(피드백) | `…/item/grouping_corrections.json` · `review/dataset_corrections.json` | — | gitignore |
| 검수 HTML | `review/*.html` | grouping_editor · label_inspect · infer_photo 등 8종 | gitignore |
| 원본/매칭 사진 | `data/image/`(raw) · `data/image_dataset/`(정리본) | — | gitignore |

⚠️ **데이터·모델·산출물은 전부 레포 밖/gitignore.** git-tracked는 `ocr_poc/ tests/ tools/ pyproject.toml`과 `docs/`뿐. 마이그레이션 시 **모델·뱅크·데이터의 이전·영속화 전략 별도 필요**(레포에 없음).

---

## 7. 테스트 상태 (2026-06-26 실측)

| 스위트 | 명령 | 결과 |
|---|---|---|
| SP1 production | `uv run pytest`(`tests/` 14파일) | **53 passed** (0.24s) |
| SP2 spike 순수코어 | `poc/bin/python -m pytest test_group.py test_rows.py` | **27 passed** (0.07s, group 24 + rows 3) |

- SP1: 합성 데이터만(실 이미지/DB 비의존), Fake 어댑터로 결정론. 새 production 코드는 `tests/test_*.py` 1:1 동반 규약.
- SP2: 순수 코어(`group.py`·`rows.segment_rows`)만 단위테스트. **IO·모델·오케스트레이터(grouping·infer_photo·train_contrastive)는 테스트 없음**(spike) → 본선 승격 시 작성 필요.

---

## 8. 미해결 / production 승격 시 할 일

| 항목 | 상태 | 마이그레이션 영향 |
|---|---|---|
| SP2 → `RecognizerAdapter` 통합 | 미착수 | 본선 ML 서비스의 핵심 작업 |
| `group_amounts.py`(§6 금액합 검산) | **미구현** | 자동 검산 게이트의 토대 |
| 합계행 자동검출 | 미구현(에디터 수동) | 검산·VLM 필요 |
| 재워프 회수 18장 | 보류(진단 완료) | 라벨 커버리지 ↑ 여지(품질 아닌 정합) |
| 그룹핑 엣지케이스: 단가숫자 품목칸 침범 | 이번 세션 신규 발견 | 교정 에디터로 우회 가능 |
| SP2 IO/오케스트레이터 테스트 | 없음 | 승격 시 작성 |

### ⚠️ 서빙 제약 — PyTorch-MPS ↔ MLX 공존 불가 (이번 세션 실측)

**한 프로세스에서 transformers ViT(품목 인코더)를 MPS forward한 뒤 MLX(Qwen) generate를 호출하면 출력이 `!!!`로 깨진다**(단순 matmul로는 안 터지고 모델 상주+forward에서 발생). `infer_photo.py`는 **품목 인코더를 CPU로 고정**해 회피(crop 소수라 ~1.6s). → 마이그레이션의 모델 서빙은 **품목(torch)·금액(MLX) 인식기를 분리 프로세스/서비스로 두거나 디바이스를 분리**해야 한다. 단일 프로세스 통합 서빙 금지.

---

## 9. 마이그레이션 연결 체크리스트 (이 문서가 답하는 것)

설계 시 아래를 이 문서로 채울 수 있다:

1. **backend ↔ ml**: ML은 §4.2 `infer_photo.process_one` 계약(사진→품목+금액 JSON)을 REST/큐로 노출. SP1은 §4.4 CLI(`run_pipeline` 순수 오케스트레이션).
2. **worker ↔ ml**: 추론 잡 = §4.2, 재학습 잡 = §4.3(`--production` → ft_prod.pt+bank.npz). 트리거 = §5 교정 누적 임계.
3. **env 주입**: §4.5 `SJMJ_DATA_DIR`·`SJMJ_DB_BACKUP`을 컨테이너에.
4. **모델·데이터 영속화**: §6 — 전부 gitignore라 별도 볼륨/스토리지 전략 필요(ft_prod.pt 348MB·bank.npz·dataset).
5. **서빙 토폴로지**: §8 제약 — 품목(torch CPU/MPS)·금액(MLX Metal) 분리.
6. **GT 매칭키**: §5 — `total_supply`(공급가합), `grand_total` 금지.

---

> **요약**: invoice-ocr/ml의 토대(검출→그룹핑→품목인식+금액인식+검산 수직 슬라이스)는 신규 사진에 end-to-end 검증 완료(테스트 SP1 53 + SP2 27 passed). 인식기 선택도 확정(품목 contrastive +10pp, 금액 Qwen3-VL 84.8%). 남은 일은 **알고리즘이 아니라 production 승격**(SP2를 어댑터 뒤로, 피드백 루프 자동화, 모델·데이터 영속화, torch/MLX 분리 서빙). 이 문서의 §4 통합표면·§6 인벤토리·§8 제약이 마이그레이션 설계의 연결 지점이다.
