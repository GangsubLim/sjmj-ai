# SP2 품목·금액 그룹핑 라벨링 — 진행 상황 / 핸드오프 (2026-06-26)

> 다음 세션 이어받기용. **이 문서가 현재 구현의 정본**이다(원 design spec은 일부 폐기됨, 아래 §1).
> 관련 문서: 설계 `specs/2026-06-26-sp2-item-amount-grouping-design.md`(일부 superseded),
> plan `plans/2026-06-26-sp2-item-amount-grouping.md`(Path B 전환·total·db_skip 미반영),
> 상위 spec `specs/2026-06-25-sp2-handwriting-recognizer-findings.md`(§3 그룹핑 규칙 등).
> **마이그레이션 참조용 확정 구조**: `specs/2026-06-26-invoice-ocr-ml-confirmed-architecture.md`
> (통합 표면·산출물 인벤토리·테스트·서빙 제약 — migration 연결 설계의 진입점).

## 0. 목표

손글씨 거래명세서의 **품목·금액 라벨링 품질**(박스 위치/크기 + 그룹핑) 개선 → DB명으로 라벨된
클린 품목-이미지 라벨셋 확보. 두 결함 수정:
- **결함1**: 품목 박스가 글자 없는 곳에서 시작 → **ink-snap**으로 해결.
- **결함2**: 빈 그룹핑칸이 정상 박스로 오인식 → **이중신호 분류**로 해결.
- 추가: 품목·금액을 엮은 **그룹핑**(§3 약식분해), **합계행**, 합쳐쓴 항목 **DB명 건너뛰기**.

## 1. 설계 변경 (중요 — 원 spec과의 차이)

원 design spec §2의 **"금액칸 척추"** 접근은 **폐기**했다. 74장 census에서 금액칸 stroke 프로파일이
**인쇄 가로 격자선을 행으로 오검출**(status ok 6/74)하는 게 드러남(진단으로 확정).

→ **Path B 채택**(사용자 승인): 검증된 `canon` φ-그리드(행 위치, labelset 68/74 입증)로 행을 잡고,
거기에 **이중신호 분류 + ink-snap 박스**를 적용. 금액칸은 행 척추가 아니라 amt_ink 신호·§6 합검증에 사용.
보강: **가로선 제거**(셀 경계 인쇄선 배제), **trim_to_data_block**(하단 노이즈 배제).

## 2. 구현 컴포넌트 (`apps/invoice-ocr/ml/report/sp2_spike/item/`, 전부 gitignore)

| 파일 | 역할 | 층 |
|---|---|---|
| `group.py` | **순수 코어** — `classify_types`·`trim_to_data_block`·`form_blocks`·`snap_box_v`·`_assemble`·`build_proposal`·`apply_corrections`·`proposal_to_dict`. `ROW_NEW/CONT/EMPTY/TOTAL`, `Row`/`Proposal` frozen DTO | 순수 |
| `rows.py` | `detect_grid_rows`(φ-그리드, HEADER_ROWS=3 skip)·`band_features`(가로선 제거 후 item/amt ink·snap용 stroke)·`_ink_mask`/`_remove_hlines`. `segment_rows`등 금액프로파일 유틸은 보존(미사용) | 이미지→배열 |
| `grouping.py` | 오케스트레이터 — `rectify_warp`·`propose`·`all_warps_and_pitch`·`main`(census). 임계 `ITEM_MIN=0.04`·`AMT_MIN=0.045`·`PAD=3` | 오케스트레이터 |
| `group_editor.py` | 교정 에디터 → `review/grouping_editor.html`. 1차 검수안 resume·DB명 건너뛰기 UI·합계 전폭박스·플래그/필터·export | IO/HTML |
| `dataset_build.py` | `build_labelset_grouped()`(신규, `--grouped`) — 교정 GT → `report/dataset_grouped/<DB명>/` 크롭 | IO |
| `photomatch.py` | 사진↔DB 매칭(보강: 기존 confirmed 시드·NEW 태그·HTML을 `review/`로) | IO |
| `review_flags.json` | 워프불량(rewarp)·제외(exclude)·노트(note) | 데이터 |
| `grouping_corrections.json` | 2차 검수 결과(`{cname:{bands,types,db_skips}}`) | 데이터 |
| `test_group.py`(24)·`test_rows.py`(3) | 순수 단위테스트 — **27 passed** | 테스트 |
| `group_amounts.py` | **미구현**(§6 금액합 검증, Task 7) | — |

## 3. 핵심 알고리즘 / 결정

- **이중신호 분류**(`classify_types`): `amt_ink < AMT_MIN` → `empty`; 있으면 `item_ink ≥ ITEM_MIN` → `new`,
  아니면 `cont`(위 블록 합산 = §3 약식분해). 빈 합산칸이 new로 안 잡힘(결함2).
- **trim_to_data_block**: 상단 첫 데이터행부터 첫 빈행까지 연속 블록만 유지, 그 아래(합계·전화·메모) 배제.
  inv054 진단(상단 2품목 + 하단 전화번호/합계 13행 오검출)에서 도출 → 17→41/74 점프의 결정타.
- **가로선 제거**(`_remove_hlines`): 국소대비 ink 마스크에서 전폭 수평성분 형태학적 제거 → 셀 경계
  인쇄선이 ink·snap을 오염시키는 것 차단. (금액칸 격자선 오검출 교정)
- **ink-snap 박스**(`snap_box_v`): 밴드 내 stroke 첫/끝 행에 세로 스냅 + PAD. 결함1 해결.
- **total 타입**: 합계행. 블록 비참여(form_blocks·trim에서 중립), crop 제외, **박스 좌측 품목영역까지 전폭**
  (`_assemble`에서 box=(y0,y1), 소비측에서 ITEM_X[0]~AMOUNT_X[1] 렌더/크롭). 자동검출 안 함 → 에디터 수동.
- **db_skip**: 손글씨 한 행에 두 DB항목이 합쳐 적힌 경우(예 inv192 베어링대+공임, inv193 부동액+공임),
  손글씨 행이 없는 DB명을 건너뜀. `available = [name for i,name in enumerate(db_names) if i not in db_skips]`,
  블록 j → available[j]. status ok iff `n_blocks == len(available)`.
- **상수**: 전역 피치 `P≈81`, `ITEM_MIN=0.04`·`AMT_MIN=0.045`(74장 스윕 최적), `PAD=3`, `HEADER_ROWS=3`.
- **워프 좌표**(grid_v4): `ITEM_X=(100,392)`·`AMOUNT_X=(612,896)`·`DATA_Y=(612,1948)`·`WARP 900×2100`.
- **흐림 격자 회수**(per-sheet, 2026-06-26): 과노출로 청색격자가 바래 `blue_mask`(b−r>10)가 무너진
  전표는 코너검출이 아니라 **hline 검출 실패**가 근본원인(실측: inv038 1선 vs 대비향상 14선).
  `grid_v4.blue_mask_enh`((b−r) 양수부 per-image 정규화+CLAHE+적응threshold)로 복구. 단 **전역 적용은
  금지** — 흐림이 흔해(91중 29장 표준<10선) 정상 전표 위상까지 흔들어 trusted 66→63·crop 265→244 회귀
  실측. 따라서 `grid_v4.faint_on` 컨텍스트(기본 OFF)로 **review_flags `faint` 명단 전표를 처리하는 동안에만**
  켠다(`hline_ys`가 대비향상 마스크를 쓰되 DATA_Y 선이 더 많을 때만 채택). 오케스트레이터(grouping·
  dataset_build·group_editor)가 per-sheet `with faint_on(cn in FAINT)`로 배선. **무회귀**(정상 68장 byte-identical).
  흐림 회수 전표는 set-aside 하지 않고 정상 harvest 경로를 탄다. ⚠️ 회수 시 워프가 바뀌어 **기존 stale 교정
  무효** → grouping_corrections에서 해당 항목 제거(백업 `.bak`) 후 auto 또는 에디터 재교정.

## 4. 데이터 상태

- **91 전표**: 기존 74 + 2026 신규 19(2026-04~06), inv54·59 제외(사용자 지시).
  - `data/image_dataset/manifest.json` = DB GT(`items` = DB invoice_items).
  - 매칭: `photomatch.py`(사진↔DB) → `confirmed_matches.json`(56) → `dataset_build.py`가 manifest 생성.
- **매칭 제외 9장** → `data/image/_excluded/`(DB 대응 없음. 단 inv479·483은 같은날 다른 정상사진으로 유지).
- **교정**: `grouping_corrections.json`(2차, 87전표) — **db_skips inv192=[3]·inv193=[3]**. 흐림 회수 4장
  (inv045·074·187·048)의 stale 교정은 제거됨(백업 `.bak`; 깨진 워프 기준이라 무효).
- **review_flags.json**: rewarp **18장**, **faint 4장**(inv045·074·187·048 흐림 회수 — §3 흐림격자 회수), exclude **1장**
  (inv144 사진≠발행본), note 7장(inv049 잘림·inv075/192/193 합쳐쓰기·inv444 오크롭·inv038/131 과노출 회수실패).
  - rewarp 목록(18): inv038,042,131,135,153,177,209,222,223,444,449,452,465,468,470,482,098,102.

## 5. 결과 (라벨셋 수확)

`dataset_build.py --grouped` → `report/dataset_grouped/<DB명>/<cname>_<db_idx>.png`

| 지표 | 그룹핑 라벨셋 | 기존 dataset_v2(비그룹핑) |
|---|---|---|
| trusted 전표 | **70** | 68 |
| 크롭 | **280** | 314 |
| 고유라벨 | **135** | 142 |
| 2+표본(few-shot) | **39** | — |

흐림 회수(2026-06-26)로 66→**70** 전표·265→**280** 크롭(+4전표·+15크롭·+5라벨·+2 few-shot).
회수 4장 크롭↔라벨 정합 육안검증됨(inv074 쇼바·엔진오일, inv187 마그넷·베어링·원터치·축밸브 매칭).
나머지 적은 건 품질 아닌 정합: 워프불량 18+제외 1 배제 · 그룹핑 연속행 비크롭 · 합계 제외.
검증: db_skip 정확(inv193→`드라이`, inv192→`베어링소`).

## 6. 미해결 / 다음 단계 (우선순위순)

1. **재워프 회수**(흐림 4장 회수 후 **일단 보류** — 사용자 지시로 라벨 쪽 우선). 22장 진단 완료(`rewarp_diag.py`·`rewarp_montage.py`, 후보 quad별 rect_score 시각화).
   근본원인이 이질적임을 실측: 서베이(`docs/research/2026-06-26-image-rectification-dewarp-survey.md`) 적용성 평가 결과
   헤드라인 권고(#1 학습 세그·#2 piecewise 휨)는 22장 주레버 아님(정상 68장 금액열 curl용).
   - ✅ **흐림격자 4장 회수 완료**(inv045·074·187·048) — §3 흐림 회수. +15크롭.
   - ❌ **흐림 회수 실패 2장**(inv038·131): 과노출이 심해 격자뿐 아니라 손글씨까지 흐려 ITEM ink 미검출(blk0).
     ink 임계 완화 or VLM 필요. (review_flags note 기록)
   - **2장 동시촬영 1장**(inv042): `twoup.py` 수동분할(`twoup_split.json` 1줄 지정)로 회수 가능 — 도구 이미 존재.
   - **책상 비스듬/배경혼동 1장**(inv444): area 0.15, 영수증영역 못잡음. 서베이 #1(형태 세그) 부분도움(paper마스크
     7→14선)이나 잔여 전단 → 수동박스 or 실 doc-seg 모델(torch 가용). 가장 어려움.
   - **워프 멀쩡, 그룹핑/스냅만 ~14장**(inv452 등 20선): 워프 문제 아님 → 에디터 재교정만으로 회수 가능.
2. **needs_review 2장**(비 set-aside) — 3차 미세교정 시 +2 전표.
3. ✅ **few-shot 인식 평가 완료**(spec §8-B, 2026-06-26) — `dataset_grouped` 280 crop·재현 39로 leave-one-invoice-out.
   인코더 비교: **`ddobokki/ko-trocr` top-1 47.3%/top-3 58.7%** 압도적 우세(영어 TrOCR 29.9/47.8, team-lucid small 27.7/42.4,
   daekeun-ml nsmc 22.8/37.5). 발견: **OCR 도메인 한글 인코더**여야 함("한글이면 다 좋다" 아님). crop 품질 개선에도 top-1
   평탄 → **인코더가 천장** 확정. → ✅ **작성자 파인튜닝 완료(§9)**: 검수 게이트 + contrastive 메트릭 러닝.
4. **§6 금액합 검증**(`group_amounts.py` 미구현, plan Task 7) — 블록합==DB금액 정합 + 합계행 자동검출 토대(VLM 필요).
5. **spec/plan 문서 정리** — design spec §2(금액척추)·plan Task 4~8을 Path B·total·db_skip 실제 구현으로 갱신.

## 7. 실행 방법

```bash
PY=/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/poc/bin/python
cd apps/invoice-ocr/ml/report/sp2_spike/item

$PY -m pytest test_group.py test_rows.py -q     # 순수 테스트 (27 passed)
$PY grouping.py                                 # 74→91장 census (status ok 수)
$PY group_editor.py                             # 교정 에디터 → review/grouping_editor.html
$PY photomatch.py                               # 사진↔DB 매칭 → review/photomatch_review.html
$PY dataset_build.py --grouped                  # 라벨셋 수확 → report/dataset_grouped/ + review/grouped_labelset_review.html
$PY dataset_build.py                            # (구) 매칭→manifest 재빌드 + 비그룹핑 labelset
$PY fewshot.py ddobokki/ko-trocr ../../dataset_grouped     # few-shot 인식 평가(인코더 인자; ddobokki=47.3/58.7%)
$PY rewarp_diag.py                              # 재워프 후보 진단 → report/rewarp_diag.html
```

검수 루프: 에디터에서 행타입(클릭 순환 new→cont→total→empty)·DB명칩(클릭=건너뛰기) 교정 →
`export corrections`(`grouping_corrections.json` = `{bands,types,db_skips}`) → `item/`에 반영 → `--grouped` 재실행.

## 8. 파일 위치 (전부 gitignore, 문서 제외)

- 코드: `apps/invoice-ocr/ml/report/sp2_spike/item/`
- 검수 HTML: `apps/invoice-ocr/ml/review/`(grouping_editor·photomatch_review·grouped_labelset_review)
- 라벨셋: `apps/invoice-ocr/ml/report/dataset_grouped/`
- 원본/매칭: `data/image/`(raw, `_excluded/` 포함), `data/image_dataset/`(정리본+manifest)
- 문서(git-tracked): `docs/superpowers/`
- ⚠️ spike 코드는 gitignore(로컬 전용). 커밋은 문서(`docs/`)만.

## 9. 작성자 파인튜닝 (contrastive 메트릭 러닝) — 2026-06-26

few-shot 평가에서 인코더가 천장으로 확정(§6.3)된 뒤, `ddobokki/ko-trocr`를 베이스로 작성자 손글씨를
DB 정식명으로 군집시키는 임베딩을 contrastive 학습. **학습 전 라벨 품질 검수 게이트**를 먼저 통과시킴
(오염된 정답으로 학습하면 군집이 오염을 베낌).

### 9.1 검수 게이트 (`label_inspect.py` → `review/label_inspect.html`)
`dataset_grouped`(280 crop)를 `build_labelset_grouped`와 **동일 walk로 메모리 재생성**해 라벨별로 펼친
**인터랙티브 검수판**. 3차원 자동 신호 + 사람 판정:
- **임베딩 outlier**(ddobokki 클래스 중심거리 + 최근접 타클래스) → 오답·불량 crop 통합 검출(outlier 20·closer 7).
- **병합 후보**(라벨명 접두관계·문자열 유사도) → 관용어/축약 변형(46쌍).
- **넓은 strip**(품목칸 좌우 여백) → 잘림/overflow를 사람이 직접 확인. ⚠️ **자동 overflow 기하검출은 폐기**:
  품목칸 인쇄 세로선이 stroke 마스크(국소대비<−28)를 통과해 [품목─선─이웃칸]을 한 덩어리로 이어
  경계기반·연결성분 어느 쪽도 overflow와 이웃칸을 분리 못 함(거짓양성 41~61%). strip+임베딩 outlier로 대체.
- 판정 export(`review/dataset_corrections.json`, localStorage 자동저장·preload 재개): **drop·ditto·relabel·merge**.
- 검수 결과(작성자): **drop 8 · ditto 1 · relabel 10 · merge 5**. → clean **271 crop** · 122 라벨 · 재현 39 · 67 전표.
  교정 적용 순서 = **relabel(crop별) 먼저 → merge(라벨 변형, transitive) 나중**(예: crop→`가스겟`(relabel)→`가스겟트`(merge)).

### 9.2 학습기 (`train_contrastive.py`)
- **베이스** ddobokki ko-trocr ViT(768d·12층). **embeddings+layer[0:10] 동결, 마지막 2층+layernorm+128d
  projection head만** 학습(14.4M·소량 과적합 방지). ⚠️ **fp32 로드 필수**(기본 fp16 → fp32 head와 MPS matmul dtype 충돌).
- **손실** SupCon(temp 0.07, 2-view). **손글씨-안전 증강**: 회전±6°·이동·스케일·shear·밝기/대비·블러,
  **좌우/상하 반전 없음**(글자 정체성 보존).
- **평가** = 전표 단위 hold-out → 학습 뱅크에 retrieval(누수 없는 K-fold). **early-stop·평가는 projection**
  (학습되는 메트릭 공간·배포 임베딩; backbone은 49.5→50.0으로 거의 안 움직여 projection head가 학습 전담).

### 9.3 운영 시나리오 정정 (중요)
실제 플로우 = **사진 → 인식기가 품목·금액 인식 → UI 표시 → 사용자 검수·수정 → DB 등록**.
즉 **추론 시점에 DB는 비어 있음** → "전표를 grand_total로 DB매칭해 후보군을 안다"는 가정은 **성립 안 함**
(처음 만든 per-invoice 후보군 제약은 폐기). 따라서 **개방 retrieval(작성자 어휘 전체 대상)이 곧 운영 메트릭**.

### 9.4 결과 (4-fold 교차검증, 184 crop 채점 · 개방 retrieval)
| | top-1 | top-3 | top-5 |
|---|---|---|---|
| 베이스라인 동결 backbone | 49.5% | 63.6% | 67.9% |
| **파인튜닝 projection** | **59.8%** | **72.8%** | **76.6%** |
| Δ | **+10.3pp** | **+9.2pp** | **+8.7pp** |

- **파인튜닝 robust하게 +9~10pp**(단일 split 운빨 아님 확인). 베이스라인 49.5%는 역사적 글로벌 47.3%와 부합.
- **신뢰도 게이팅 자동채움**(코사인 top1유사도 순): 정밀도 ≥95% → 커버리지 **6%**, ≥90% → **28%**.
  무인 자동확정은 제한적(코사인 신뢰도 약함). 단 이 플로우는 사용자 검수 전제라 **pre-fill + top-5 드롭다운이 실효 가치**.
- **커버리지 한계(정직)**: 채점 184는 재현 품목뿐. 전체 271 중 ~32%는 첫 등장(신규)이라 retrieval 불가→타이핑.
  **DB가 쌓일수록 신규→재현 전환·뱅크 확대로 자동 개선**(작성자 특화 학습의 핵심).
- **정직 평가**: 재현 행 타이핑을 ~60% 줄이는 **HITL 입력보조로 유효**, 무인 자동화 수준은 아님(흘림체+소량 271 crop 천장).

### 9.5 배포 산출물
- `runs/ft_prod.pt` — **배포 모델**(전체 271 crop·22 epoch 고정 학습). `train_contrastive.py --production`.
- `runs/bank.npz` — **배포 뱅크**(전체 crop projection 임베딩 + 라벨/전표/key). 추론 = 신규 crop 임베딩→뱅크 retrieval.
- `infer_demo.py` → `review/infer_demo.html` — 실제 전표 행에 돌린 데모(leave-one-invoice-out, crop+정답+top-5).
  데모 6전표 41행 top-1 71%/top-3 88%(공통품목·풀뱅크라 §9.4 CV보다 낙관).

### 9.6 실행 방법
```bash
$PY label_inspect.py --embed                    # 검수판 → review/label_inspect.html (사람이 Export → review/dataset_corrections.json)
$PY train_contrastive.py --folds 4              # 4-fold 신뢰 평가 → review/finetune_report.json
$PY train_contrastive.py --production           # 배포 모델+뱅크 → runs/ft_prod.pt · runs/bank.npz
$PY infer_demo.py [inv166 ...]                  # 추론 데모 → review/infer_demo.html
```

### 9.7 다음 레버 (정확도 향상 시)
- **데이터 확대**(가장 근본): 보류한 재워프 18장 회수·2장촬영 수확 → crop·전표 커버리지 ↑. 운영에서 DB 쌓이며 자동 개선.
- **금액/단가 신호 결합**: 품목별 가격 prior로 후보 narrowing(symbolic 검산 레이어 §6와 연계).
- **학습 튜닝**: 더 많은 층 unfreeze·다른 손실(ArcFace/multi-sim)·인코더 앙상블(ddobokki+영어TrOCR)·증강 강화.
