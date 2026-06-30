# OCR 교정 큐레이션 → 재학습 연결 설계

수기 거래명세서 OCR의 라이브 HITL 교정(`ocr_corrections`)을 품목 인식기 재학습으로 잇고,
재학습 직전 사람이 단계별 인식 결과를 검토·정리하는 **큐레이션 게이트**를 신설한다.

## 1. 배경과 문제

### 1.1 현황 (코드 확인)

이미 동작하는 것:

- **추론 파이프라인**: 사진 업로드 → `ocr_jobs`(pending) → ml-worker 폴링
  → `form_quad_robust`→`warp`→`deskew/rotate`→`detect_grid_rows`→`embed_crops`(품목 ViT retrieval)
  →`read_amount`(Qwen3-VL) → `assemble_result_json` → `ocr_jobs.result_json`(done).
  crop PNG는 `SJMJ_DATA_DIR/ocr_crops/job-{id}/row-{i}.png`로 저장
  (`handwriting/infer_job.py:77`).
- **검수·수정·저장**: 프론트 `invoice-form.tsx`가 top-5 드롭다운으로 수정을 받고,
  `POST /api/ocr/jobs/{id}/confirm`이 invoice 생성 + `ocr_corrections`에 draft↔final diff 적재
  (`app/services/ocr_correction.py:build_correction`). **원본(`result_json`)은 불변,
  수정은 별도 테이블 — 덮어쓰기 아님.**
- **재학습 코드**: `handwriting/dataset_build.py` → `handwriting/train_contrastive.py`
  → `ft_prod.pt`/`bank.npz` 존재. macmini 수동 실행(ADR 0003).

### 1.2 끊긴 부분 (이번 작업 범위)

1. **라이브 교정이 재학습에 닿지 않는다.** `train_contrastive`가 먹는 grouped 라벨셋은
   `dataset_build`가 OneDrive/`data/image` backfill 소스로 만든다. 라이브 산물인
   `ocr_corrections` / `ocr_crops/` PNG는 입력에 없다.
2. **`ocr_corrections` 조회/export 경로가 없다.** 쌓이기만 한다.
3. **재학습 직전 검토 화면이 없다.** confirm 1차 검수 이후, 누적 교정을 단계별로 보고
   배제·정규화하는 2차 관문이 없다.
4. **단계별 시각화용 산출물 일부 미저장.** `result_json`은 최종 rows + `warp_ok`(bool)만.
   워프된 전표 전체 이미지가 저장되지 않는다(`infer_job.py:64`가 `w`를 만들지만 버림).

### 1.3 도메인 사실 (불변)

- **재학습 대상은 품목 인식기 하나.** 금액 인식기(Qwen3-VL)는 추론 전용 — 학습 안 함
  (CONTEXT.md, ADR 0002).
- 학습 정답지의 단위 = `(row crop 이미지 → 확정 품목 라벨)` 쌍.

## 2. 핵심 결정

상세 근거는 `docs/adr/0004-curation-gate-and-training-pairs-read-model.md`.

| 결정         | 내용                                                                                            |
| ------------ | ----------------------------------------------------------------------------------------------- |
| 야심 수준    | 두 끝점(원본 crop+최초 인식 ↔ 최종 수정) 기반 학습데이터 큐레이션. 중간 텐서 디버그 뷰는 비범위 |
| 학습 후보    | confirm된 **모든** 행이 후보(맞춘 행도 양성 라벨). 변경분은 강조 표시                           |
| 금액         | 읽기전용 행 맥락(학습 비대상 명시). 큐레이션 액션은 품목 라벨에만                               |
| 데이터 흐름  | `dataset_build` 확장으로 **단일 학습 진입점**. 페이지=결정 저장 / 학습=macmini 수동 CLI         |
| 라벨 정규화  | 큐레이션이 정식명 재매핑/병합 담당 — `grouping_corrections.json` 손편집 계승                    |
| 데이터 모델  | `training_pairs` 머티리얼라이즈 read-model (오버레이 아님)                                      |
| 페이지 축    | 잡(명세서) 단위 드릴다운 1차. 라벨 그룹 일괄 병합 뷰는 후속                                     |
| 게이트       | 기본 `included`+배제, **검수 완료(reviewed) 잡만 export**                                       |
| 이미지       | 전용 read 엔드포인트, 정수 키 파싱(path traversal 차단)                                         |
| invoice 분리 | invoice 불변. `canonical_label`(학습용)과 `invoice_items.name`(청구 사실)은 갈라질 수 있음      |

## 3. 데이터 모델

### 3.1 신규 테이블 `training_pairs`

confirm된 행마다 한 행. crop_ref는 job id를 품어 전역 유니크(`job-{id}/row-{i}`).

```sql
CREATE TABLE training_pairs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    crop_ref VARCHAR(64) UNIQUE NOT NULL,   -- "job-42/row-0"
    job_id INT UNSIGNED NOT NULL,
    invoice_id INT,
    row_index INT NOT NULL,
    draft_label VARCHAR(200),               -- 모델 top-1 (item_top5[0].label)
    final_label VARCHAR(200),               -- confirm 시 사용자 입력명 (불변 스냅샷)
    canonical_label VARCHAR(200),           -- 학습용 정규화 라벨 (기본 = final_label)
    supply INT,                             -- 행 식별용 읽기전용 맥락
    status VARCHAR(16) NOT NULL DEFAULT 'included',  -- included | excluded
    reviewed_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_training_pairs_job (job_id),
    INDEX idx_training_pairs_canonical (canonical_label),
    INDEX idx_training_pairs_status (status),
    CONSTRAINT fk_training_pairs_job FOREIGN KEY (job_id)
        REFERENCES ocr_jobs(id) ON DELETE CASCADE,
    CONSTRAINT fk_training_pairs_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 3.2 `ocr_jobs` 확장

```sql
ALTER TABLE ocr_jobs ADD COLUMN curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE;
```

잡을 검수 완료로 넘기면 `TRUE`. export는 `curation_reviewed=TRUE` 잡의
`status='included'` 쌍만 본다.

### 3.3 머티리얼라이즈와 백필

- **신규 confirm**: `OcrService.confirm`이 `insert_correction` 직후, `build_correction`이
  계산한 `lines[]`로 `training_pairs`를 라인별 insert(`canonical_label` 초기값=`final_label`,
  `status='included'`). 같은 트랜잭션 안에서.
- **기존 잡 백필**: 마이그레이션이 기존 `ocr_corrections.correction_json.lines[]`를 읽어
  `training_pairs`로 1회 적재. crop_ref·draft_label·final_label·supply를 그대로 옮긴다.
- `ocr_corrections`는 confirm 시점 감사 로그로 그대로 보존(역할 분리).

### 3.4 스키마 반영 위치 (운영 + 테스트 하니스 — 둘 다 필수)

이 repo의 백엔드 테스트는 migration을 실행하지 않고 `tests/fixtures/schema_test.sql`을
세션 시작에 직접 로드하며(`tests/conftest.py:48`), 매 테스트마다 고정 `_ALL_TABLES`만
TRUNCATE한다(`tests/conftest.py:19`). 따라서 §3.1·§3.2 스키마 변경은 **두 곳**에 반영한다:

- **운영**: `db/migration_008_*.sql`(직전이 `migration_007_ml_seam`) — `training_pairs`
  CREATE + `ocr_jobs.curation_reviewed` ALTER + `ocr_corrections`→`training_pairs` 백필.
- **테스트 하니스**: `tests/fixtures/schema_test.sql`에 `training_pairs` CREATE +
  `ocr_jobs`에 `curation_reviewed` 컬럼 추가, 그리고 `conftest.py:_ALL_TABLES`에
  `training_pairs` 추가(미추가 시 테스트 간 TRUNCATE 격리에서 누락). 백필 SQL은 운영 전용이라
  fixture에는 넣지 않는다(테스트는 confirm 머티리얼라이즈 경로로 데이터를 만든다).

## 4. 백엔드 큐레이션 슬라이스

`router → service → repository` 3계층. **첫 Pydantic 슬라이스**이므로 선결로
`RequestValidationError`→400 envelope 핸들러를 `app/core/errors.py`에 도입한다
(현재 `AppError`+`Exception`만 등록 — `errors.py:54-55`). 422를 외부 계약 불변식인
400 envelope(`{success:false, error:{code:"VALIDATION_ERROR", message, details:{필드:메시지}}}`)로
변환한다.

### 4.1 엔드포인트

| Method · Path                                  | 용도                                                                                                                             | req/res                    |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| `GET /api/curation/jobs`                       | 검수 큐 — confirmed 잡 목록(검수상태·미처리수 포함, 페이지네이션)                                                                | → 목록                     |
| `GET /api/curation/jobs/{job_id}`              | 잡 상세 — 단계 이미지 ref + 행별(crop_ref·top5·draft/final/canonical·supply·status)                                              | → 상세                     |
| `PATCH /api/curation/pairs/{id}`               | 쌍 큐레이션 — `status` 또는 `canonical_label` 갱신 (`id`=`training_pairs.id` 정수; crop_ref는 슬래시 포함이라 path param 부적합) | CurationPairPatch → 갱신쌍 |
| `POST /api/curation/jobs/{job_id}/review`      | 잡 검수 완료 표시(`curation_reviewed=TRUE`, 미처리 쌍 `reviewed_at` 스탬프)                                                      | → ack                      |
| `GET /api/curation/jobs/{job_id}/image/{kind}` | 원본/워프 전표 이미지(kind=original\|warped)                                                                                     | → FileResponse             |
| `GET /api/curation/jobs/{job_id}/crop/{row}`   | 행 crop 이미지                                                                                                                   | → FileResponse             |

이미지 엔드포인트는 `job_id`(정수)·`row`(정수)·`kind`(enum)만 받아 서버에서
`SJMJ_DATA_DIR` 하위 경로를 조립한다. crop_ref 문자열을 raw 경로로 신뢰하지 않는다.
없는 산출물(예: 백필된 구 잡의 `warped.png`)은 404 — 프론트가 graceful degrade.

### 4.2 응답 계약

JSON 엔드포인트는 외부 계약 불변식 유지: 성공 `{success, data, pagination?}`, 에러
`{success, error:{code, message, details?}}`, 에러코드 체계
(`VALIDATION_ERROR`/`NOT_FOUND`/`CONFLICT`/`SERVER_ERROR`), 검증 실패 400.

**이미지 2종(`image/{kind}`·`crop/{row}`)은 success envelope의 명시적 예외**다 —
`FileResponse`로 raw 바이트(`image/png`)를 반환하며 `{success,data}`로 감싸지 않는다.
에러(404 등)는 여전히 에러 envelope로 낸다. 이 예외를 `.claude/rules/api-conventions.md`와
`api-spec.json`(아래)에 명시한다.

### 4.3 api-spec.json 동기화 (Phase A 산출물)

`.claude/ai-context/api-spec.json`이 엔드포인트 SSoT이므로(`api-conventions.md:18,179`),
이 슬라이스 산출물에 다음을 포함한다 — 드리프트 차단:

- `paths`: `/api/curation/*` 6개 엔드포인트(§4.1) 추가.
- `components.schemas`: `CurationJobSummary`·`CurationJobDetail`·`CurationPair`·
  `CurationPairPatch` 등 req/res 모델 추가.
- `x-api-overview.endpoints`: 6개 한 줄 스캔 항목 추가. 이미지 2종은
  `res: "binary image/png (raw FileResponse, envelope 예외)"`로 표기.

## 5. worker 변경

`handwriting/infer_job.py`: 워프 전표 `w`를 `crop_out_dir/warped.png`로 추가 저장
(현재 `w`는 line 64에서 만들어 버려짐). 한 잡당 1장. 나머지 추론 경로 불변.
warp 실패(`quad is None`) 시 저장 안 함 → 이미지 엔드포인트 404.

## 6. 프론트엔드 큐레이션 페이지

새 라우트(예: `/curation`). 기존 페이지 패턴(`use-*` 훅 + `services/api.ts`) 따름.

- **큐 목록**: confirmed 잡들, 검수상태·미처리수. 미검수 잡 우선.
- **잡 드릴다운**(사용자 묘사 레이아웃):
  - 단계 before/after: ① 원본 업로드 → ② Warp(원본↔warped.png).
  - 행별: crop 이미지 + 품목 top5(최초 인식) → `canonical_label` 편집(item_suggestions
    자동완성 + 유사 기존 라벨 힌트) + 배제 토글 + 금액(읽기전용, "학습 비대상" 배지).
  - 변경된 행(`label_changed`)·재정규화된 행 시각 강조.
  - **검수 완료** 버튼 → 잡 풀림.

## 7. ML 브리지 CLI

> 코드 리뷰로 밝혀진 현실(아래)에 맞춰 Phase C를 재정의한다. "dataset_build가 디렉터리에
> 병합 → train_contrastive 무변경 소비"는 **틀린 전제**였다.

### 7.1 현재 학습 입력의 실제 모습 (확인됨)

- `train_contrastive`는 `report/dataset_grouped/` 디렉터리를 **읽지 않는다**.
  `train_contrastive.py:94`가 `from label_inspect import build_rows; rows = build_rows()`로
  **메모리에서 동일 walk 재생성**한다(`prepare()`). 학습 입력 SSoT = `build_rows()`(워프 walk)
  - 교정 JSON(`drop`/`ditto`/`relabel`/`merge`). grouped 디렉터리는 사람 검수(label_inspect
    HTML)용일 뿐 학습 입력이 아니다.
- `label_inspect.build_rows`/`grouping`/`canon`/`fewshot` 등 학습 의존 체인은 git-tracked
  `handwriting/`이 아니라 **gitignore된 `report/sp2_spike/item/`**(SP2 스파이크)에 산다.
- `dataset_build.py:33`·`photomatch.py:26`은 `ML = Path("/Users/gangsub/...")` 개발자 로컬
  절대경로 하드코딩 + 고정 SQL 덤프(`data/db-2026-06-24-backup.sql`) 의존 — AGENTS.md의
  "하드코딩 금지·.env 주입" 규약을 이미 위반.

즉 "production"이라던 재학습 경로가 실제로는 미추적 legacy 코드 + 하드코딩 경계에 의존한다.
Phase C는 단순 소스 추가가 아니라 **이 경계를 production으로 끌어올리는 작업**을 포함한다.

### 7.2 Phase C 선결 조건 (반드시 먼저)

1. **학습 입력 SSoT 확정**: 둘 중 택1을 Phase C 착수 시 결정한다.
   - (a) `train_contrastive`가 **명시적 dataset source**(env 주입 디렉터리 또는 manifest)를
     읽도록 바꾸고, 그 source에 `training_pairs` crop을 포함한다. 또는
   - (b) `label_inspect.build_rows` walk를 **production 경로로 승격**(handwriting/로 이동,
     git-track)하고, 그 동일 walk에 `training_pairs`(reviewed+included) crop을 합류시킨다.
   - 권장: (b) — 기존 학습기 동작(동일 walk + 교정 JSON 캐시)을 보존하면서 라이브 crop을
     같은 파이프에 흘린다. 단 의존 체인(`grouping`/`canon`/`fewshot`) 승격 범위를 plan에서
     좌표 확정.
2. **경계 하드닝**: `dataset_build`·승격 대상의 하드코딩 절대경로를 `SJMJ_DATA_DIR`/`SJMJ_ML_*`
   env로 교체, macmini 경로 존재 검증, legacy 고정 SQL 덤프 의존을 live `DB_*` 로더로 대체하거나
   격리한다(라이브 교정은 덤프가 아니라 운영 DB에서 읽는다).

### 7.3 브리지

선결 조건 위에서, `curation_reviewed=TRUE` 잡의 `status='included'` 쌍을 운영 DB에서 읽어
`ocr_crops/` PNG를 `canonical_label`로 라벨링해 학습 walk에 합류시킨다(교정 JSON의
`relabel`/`merge`와 동일 의미축). `train_contrastive`는 7.2(a)/(b) 결정에 따라 최소 변경.

재학습 *실행*은 ADR 0003대로 macmini 수동 CLI. 자동 트리거·페이지 주도 실행은 비범위
(read-model이 향후 확장의 토대).

## 8. 단계 분할 (독립 PR)

- **Phase A — 데이터 척추 + API**: §3(테이블·백필·머티리얼라이즈, \*\*§3.4 운영 migration_008
  - schema_test.sql + `_ALL_TABLES` 동기**) + §4(슬라이스·Pydantic 400 핸들러·이미지
    엔드포인트·**§4.3 api-spec.json 동기\*\*) + §5(worker warped.png). pytest로 완결 검증.
- **Phase B — 검수 페이지**: §6. Phase A API 위 UI. 라이브 e2e.
- **Phase C — 학습 브리지**: §7. **§7.2 선결 조건(학습 입력 SSoT 확정 + 경계 하드닝)**을
  먼저 처리한 뒤 §7.3 브리지 + macmini 수동 재학습 1회 실측 검증. Phase C 진입 시 plan이
  7.2(a)/(b)를 결정하고 승격 의존 체인을 좌표 확정한다.

### Phase A Definition of Done

- `db/migration_008_*.sql`(CREATE/ALTER/백필) + `tests/fixtures/schema_test.sql` 반영 +
  `conftest.py:_ALL_TABLES`에 `training_pairs` 추가.
- `.claude/ai-context/api-spec.json` paths/components/x-api-overview 갱신, 이미지 2종은
  raw-file envelope 예외로 표기. `.claude/rules/api-conventions.md`에 예외 명문화.
- `RequestValidationError`→400 envelope 핸들러 등록(첫 Pydantic 슬라이스 선결).
- ruff + pytest 커버리지 80% 게이트 통과.

## 9. 테스트

- **Phase A**: `training_pairs` 머티리얼라이즈(confirm 통합), 백필 마이그레이션,
  큐레이션 CRUD contract/unit/integration, Pydantic 400 핸들러 contract, 이미지 엔드포인트
  path-traversal 차단 + 성공 시 raw `image/png` 반환(envelope 미적용) 단위. 커버리지 80% 게이트.
- **Phase B**: vitest 단위 + playwright e2e(업로드→confirm→큐레이션→검수완료).
- **Phase C**: 학습 입력 합류 로직 단위(합성 `training_pairs` + Fake crop, env 주입 경로) —
  실모델·하드코딩 경로 비의존. macmini 실데이터 재학습은 수동 실측(테스트 아님).

## 10. 비범위 (YAGNI)

- 라벨 그룹 단위 일괄 병합 뷰(파편화가 실제 문제될 때 2차 렌즈).
- 자동 재학습 트리거·금액합 검산 게이트(ADR 0003 — 실측 후).
- 페이지 주도 학습 실행·모니터링(read-model 위 향후 확장).
- 파이프라인 중간 텐서/embedding 디버그 뷰.
- 큐레이션의 라벨 수정이 invoice로 역전파(분리 — invoice 불변).
