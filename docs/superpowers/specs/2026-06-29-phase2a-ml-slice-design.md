# Phase 2A — ML 추론 수직 슬라이스 (설계)

- 작성일: 2026-06-29
- 성격: **Phase 2 첫 실행 단위 설계 문서.** 검증된 ML PoC를 Phase 1이 남긴 이음새에 끼워, 사진 1장이 `업로드 → 추론 → 초안 → 검수 → 확정`까지 관통하는 **얇은 수직 슬라이스**를 정의한다. 자체 spec → plan → TDD 사이클의 spec 단계.
- 상위 정본: 로드맵 [`2026-06-27-sjmj-ai-phased-roadmap-design.md`](./2026-06-27-sjmj-ai-phased-roadmap-design.md) §5, ML 확정구조 [`2026-06-26-invoice-ocr-ml-confirmed-architecture.md`](./2026-06-26-invoice-ocr-ml-confirmed-architecture.md) §4·§8.
- 결정 근거: ADR [`0001`](../../adr/0001-ml-model-artifacts-live-and-train-on-macmini.md)(모델 영속화·학습 위치) · [`0002`](../../adr/0002-single-process-device-separated-ml-worker.md)(단일 프로세스 device-분리 서빙) · [`0003`](../../adr/0003-retraining-manual-first-then-automate.md)(재학습 수동-먼저). 용어는 루트 [`CONTEXT.md`](../../../CONTEXT.md) ML 도메인 글로서리.

---

## 0. 한 줄 결론

Phase 2의 첫 실행 단위는 **얇은 수직 슬라이스**다 — "ML이 실서비스에 무수정으로 끼워진다"는 가정을 가장 싸게 조기 반증한다(1B의 vertical-slice-first 재현). 알고리즘은 검증 끝났으니, 이 슬라이스는 **통합 배선**만 증명하고 production-hardening은 의도적으로 미룬다.

---

## 1. 범위

### 1.1 슬라이스에 들어가는 것 (In scope)

| # | 항목 | 한 줄 |
|---|---|---|
| S1 | **영속화 블로커 해소** | gitignore를 file-precise 안전망으로 보강 + 추론 코어를 `report/` 밖 tracked 경로로 이동 + 모델 아티팩트 macmini 고정경로 1회 수동 배치 |
| S2 | **얇은 추론 경계** | 검증된 `process_one`을 감싸 HTML 부수효과를 떼고 구조화 `result_json`을 반환하는 함수 `infer_job()` |
| S3 | **ml-worker (전용 launchd 잡)** | 품목 CPU·금액 MLX 단일 프로세스 device-분리. `ocr_jobs` DB 폴링 → 추론 → 초안 적재 |
| S4 | **업로드 API** | `POST /api/ocr/jobs`(multipart) — 사진 저장 + 잡 등록 |
| S5 | **상태/초안 조회 API** | `GET /api/ocr/jobs/{id}` — 프론트 폴링용 |
| S6 | **확정 API** | `POST /api/ocr/jobs/{id}/confirm` — 원자적 invoice 생성 + 잡 연결 + 교정 적재 |
| S7 | **작성 페이지 증강** | 사진 업로드 + `name`(top-5 드롭다운)·`supply` pre-fill + `crop_ref` 운반 |

### 1.2 의도적으로 미루는 것 (Out of scope — 하드닝/후속)

- SP2 코어의 **전면 어댑터 승격**: `RecognizerAdapter` Protocol 정렬, frozen dataclass·순수함수 규약, IO/오케스트레이터 단위테스트. **슬라이스는 최소 래핑만**(ADR 0002 정신 — 검증 코드 재사용).
- **재학습 자동 트리거 + 금액합 검산 게이트**: 수동 실행부터, 학습곡선 실측 후 자동화(ADR 0003).
- **bank 부트스트랩 확장**: 슬라이스는 기존 `bank.npz`(271 crop) 재사용. 400건 전체 확장(재워프 18장+라벨링)은 라이브 루프/하드닝.
- **프로세스 분리 서빙**: 품목 CPU forward가 처리량 병목이 되기 전엔 단일 프로세스 유지(ADR 0002).

---

## 2. 아키텍처

```
[작성 페이지(/)]
  │ ① 사진 업로드 (multipart)
  ▼
POST /api/ocr/jobs ──► $SJMJ_DATA_DIR/ocr_uploads/{uuid}.jpg 저장
  │                + INSERT ocr_jobs(status=pending, image_path)
  │ ◄── { job_id }
  │
  │        ┌──────────────────────────────────────────────┐
  │        │ ml-worker (launchd: ai.sjmj.ml-worker)        │
  │        │  기동 시 1회: 품목 인코더(CPU torch) +        │
  │        │              금액 인식기(MLX Metal) 적재       │
  │        │  loop: ocr_jobs WHERE status='pending' 폴링   │
  │        │   → running → infer_job() → result_json       │
  │        │   → crop PNG 영속화 → status=done|failed       │
  │        └──────────────────────────────────────────────┘
  │ ② GET /api/ocr/jobs/{id} 폴링 (done까지)
  ▼
[작성 페이지: name(top-5)·supply pre-fill, crop_ref 숨김 운반]
  │ ③ 사람 검수·교정
  ▼
POST /api/ocr/jobs/{id}/confirm  (한 트랜잭션)
  │   invoice 생성(invoice service 재사용)
  │   + ocr_jobs.invoice_id 연결
  │   + ocr_corrections 적재(초안 vs 최종 diff)
  ▼ ◄── { invoice_id }
```

- **잡 = 큐**: `ocr_jobs` 테이블 자체가 큐(전용 인프라 없음). 단일 모델 프로세스라 한 번에 한 잡 직렬 처리. 폴링 간격은 plan에서 상수로(기본 2s).
- **launchd 잡 2개**: `ai.sjmj.backend`(web, :8400) + `ai.sjmj.ml-worker`(추론). launchd가 KeepAlive supervisor. 커스텀 supervisor 없음.
- **venv 경계 + 큐 접속**: ml-worker는 `apps/invoice-ocr/ml/`의 무거운 venv(torch/mlx, `[ml]` extra)에서 돈다. 큐(`ocr_jobs`)에는 **라이브 MySQL로 직접 폴링**한다 — 단 `ocr_poc/db.py`는 .sql 덤프 파서일 뿐 라이브 접속 수단이 아니므로, 워커 venv에 **MySQL 드라이버(SQLAlchemy + pymysql, backend와 동일 스택)를 신규 의존으로 추가**하고 `DB_*` env로 접속한다. backend는 경량 venv 유지(torch/mlx 비의존) — 무거운 의존은 ml-worker에만.
- **§8 제약 준수**: 품목 인코더 `device="cpu"` 고정, 금액 Qwen MLX/Metal — 한 프로세스 안 device 분리(검증된 `infer_photo.main` 그대로).

---

## 3. 데이터·경계

### 3.1 env 경계 (backend config.py + ml-worker)

`SJMJ_ML_MODELS_DIR`는 ml-worker가, `SJMJ_DATA_DIR`는 backend(업로드 저장)·worker(crop 저장) 양쪽이, `DB_*`는 backend(기존)·worker(신규) 양쪽이 소비한다. backend는 `SJMJ_ML_MODELS_DIR` 비의존(모델을 들지 않음).

| env | 용도 | 미설정 시 |
|---|---|---|
| `SJMJ_ML_MODELS_DIR` | **신규.** 모델 아티팩트(`ft_prod.pt`·`bank.npz`) 루트 | ml-worker RuntimeError |
| `SJMJ_DATA_DIR` | 기존. 업로드 사진·crop 등 운영 데이터 루트 | RuntimeError |
| `DB_HOST`·`DB_PORT`·`DB_NAME`·`DB_USER`·`DB_PASS` | **ml-worker의 라이브 큐 접속.** backend `config.py`의 DB_* 규약과 동일값(같은 운영 MySQL) | 워커 기동 실패 |

- 모델/뱅크 = `SJMJ_ML_MODELS_DIR`, 운영 데이터(업로드·crop) = `SJMJ_DATA_DIR`로 분리. 절대경로 하드코딩 금지.
- **`SJMJ_DB_BACKUP`은 2A 런타임에 불필요** — 이건 ML PoC의 .sql 덤프 파서용(backfill 툴링)이고, 라이브 큐 폴링과 무관하다. 슬라이스 워커 env 계약에서 제외한다.

### 3.2 파일 영속화

- 업로드 사진: `$SJMJ_DATA_DIR/ocr_uploads/{uuid}.jpg` → `ocr_jobs.image_path`에 기록.
- 행별 crop: `$SJMJ_DATA_DIR/ocr_crops/job-{id}/row-{i}.png` → `crop_ref = "job-{id}/row-{i}"`. 재학습 GT의 이미지측.
- 모델: `$SJMJ_ML_MODELS_DIR/ft_prod.pt`·`$SJMJ_ML_MODELS_DIR/bank.npz`. 최초 1회 현 spike 산출물 수동 복사(ADR 0001).

### 3.3 gitignore 재설계 (S1 블로커)

추론 코어를 `report/sp2_spike/` 밖 tracked 경로(`apps/invoice-ocr/ml/handwriting/`)로 옮긴다. 모델이 git에 딸려 들어가지 않도록 **패턴 안전망**을 추가:

```gitignore
# ml/.gitignore — 모델·데이터 아티팩트는 코드가 아님(어디 있든 무시)
*.pt
*.npz
data/
results/
review/
report/            # 실험 잔여물 (blanket 유지)
.venv/
poc/
```

→ `handwriting/`의 코드는 tracked, 거기 섞여도 `*.pt`/`*.npz`는 절대 추적 안 됨.

---

## 4. API 표면 (신규 3종 — api-spec.json 갱신 필수)

신규 `ocr` 라우터(`app/routers/ocr.py`)는 기존 라우터와 동일하게 `include_router(ocr.router, prefix="/api")`로 마운트한다 — 따라서 모든 OCR 경로는 `/api/ocr/*`이며, `/api`를 기본으로 둔 프론트 proxy·`VITE_API_URL`과 일치한다(`/api` 예외 없음). API 라우터는 SPA catch-all보다 먼저 등록한다. 응답은 기존 envelope 규약(`{success, data}` / `{success, error}`)을 따른다. 신규 엔드포인트는 `.claude/ai-context/api-spec.json` + `api-conventions.md`에 반영한다(프로젝트 API 거버넌스).

### 4.1 `POST /api/ocr/jobs`
- 요청: `multipart/form-data`, 필드 `photo`(이미지 파일).
- 처리: 사진 저장 + `ocr_jobs` 행 등록(status=pending).
- 응답: `{ "success": true, "data": { "job_id": 42, "status": "pending" } }`

### 4.2 `GET /api/ocr/jobs/{id}`
- 응답(done): `{ "success": true, "data": { "id": 42, "status": "done", "result": <result_json> } }`
- 응답(failed): `data.status="failed"`, `data.error="<요약>"`.
- 프론트는 done|failed까지 폴링.

### 4.3 `POST /api/ocr/jobs/{id}/confirm`
- 요청 본문 = 최종 invoice payload(기존 `POST /invoices` 본문과 동형) + **각 item에 선택적 `crop_ref`**.
- 처리(한 트랜잭션, **원자적 claim**): ① `SELECT invoice_id FROM ocr_jobs WHERE id=:job FOR UPDATE`로 잡 행을 잠그고 이미 `invoice_id`가 있으면 롤백+409 → ② invoice 생성(invoice service 재사용) → ③ `UPDATE ocr_jobs SET invoice_id=:new_id WHERE id=:job AND invoice_id IS NULL`(영향행 0이면 롤백+409) → ④ `ocr_corrections` 적재 → COMMIT.
- 응답: `{ "success": true, "data": { "invoice_id": 1001 } }`
- **동시성 계약**: read-then-409가 아니라 위 행잠금 + 조건부 UPDATE로 더블클릭·재시도 동시 confirm을 원자적으로 한 번만 통과시킨다. `ocr_jobs.invoice_id`에 unique 제약이 없으므로(현 `migration_007`) 이 claim 패턴이 중복 invoice 생성을 막는 유일한 방어선이다.

---

## 5. 계약 (스키마)

### 5.1 `result_json` (ml-worker가 `ocr_jobs.result_json`에 적재)

```json
{
  "rows": [
    {
      "row_index": 0,
      "crop_ref": "job-42/row-0",
      "item_top5": [{"label": "삼겹살", "sim": 0.83}, {"label": "목살", "sim": 0.71}],
      "supply": 120000,
      "amount_raw": "120,000"
    }
  ],
  "supply_sum": 120000,
  "warp_ok": true
}
```
- `item_top5`: 최대 5개. 신규(첫 등장) 어휘로 retrieval 불가하면 빈 배열(작성자가 타이핑).
- `supply`: 금액칸 정수 파싱 결과(파싱 실패 시 `null`, `amount_raw`만).

### 5.2 `correction_json` (confirm이 `ocr_corrections.correction_json`에 적재)

서버가 초안(`result_json`)과 확정 payload(item별 `crop_ref`)를 diff해 생성:

```json
{
  "lines": [
    {
      "crop_ref": "job-42/row-0",
      "draft_label": "삼겹살", "final_label": "목살", "label_changed": true,
      "draft_supply": 120000, "final_supply": 120000, "supply_changed": false
    }
  ],
  "rows_added": 0,
  "rows_dropped": 0
}
```
- `crop_ref` 없는 최종 item = 사람이 추가한 행(`rows_added`).
- 최종 payload에서 매칭 안 된 초안 crop = 사람이 버린 행(`rows_dropped`).
- diff 생성은 **순수함수**로 분리(단위테스트 집중 대상).

---

## 6. 에러 처리

- **추론 실패**: ml-worker가 `infer_job()` 예외를 잡아 `status='failed'` + `result_json`에 에러 요약. 잡 단위 격리(한 잡 실패가 워커를 죽이지 않음).
- **워프 실패**: `warp_ok=false`로 done 처리(행 0개 가능) — 사람이 빈 폼에서 수기 입력. 추론 실패와 구분.
- **확정 트랜잭션**: invoice 생성·잡 연결·교정 적재 중 하나라도 실패하면 전체 롤백(고아 교정 방지). 기존 `db.transaction()` 경계 재사용.
- **중복/동시 확정**: §4.3의 행잠금(`SELECT ... FOR UPDATE`) + 조건부 `UPDATE ... WHERE invoice_id IS NULL`로 원자적 1회 통과. 두 번째 요청은 409. 더블클릭·재시도·동시 요청 모두 방어 — 통합 테스트로 동시 confirm 시나리오를 검증한다.

---

## 7. 테스트 전략

원본 PHP 동치 계약과 무관한 **신규 표면**이므로, 골든이 아니라 기존 slice 패턴(`tests/{contract,unit,integration}/`)으로 신규 작성(TDD, 커버리지 ≥80%).

| 대상 | 종류 | 방식 |
|---|---|---|
| 3종 엔드포인트 입출력·envelope | contract | FastAPI TestClient |
| 교정 diff 생성 | unit | 순수함수, 합성 result_json + payload |
| 잡 생명주기(pending→running→done/failed) | unit | repository/service, 실 MySQL `sjmj_test` |
| confirm 트랜잭션(생성+연결+교정, 롤백, 409) | integration | 실 MySQL |
| ml-worker 오케스트레이션 | unit | `infer_job`을 **Fake 추론**으로 주입(canned result_json) — 실모델 비의존 |

- **테스트 전제 — fixture 스키마 동기화(TDD 0단계)**: 현재 `tests/fixtures/schema_test.sql`에는 `ocr_jobs`·`ocr_corrections`가 없다(운영 `migration_007`에만 존재). repository/service 테스트가 스키마 부재로 막히므로, **두 테이블을 `schema_test.sql`에 추가하고 `conftest.py`의 TRUNCATE 목록에도 등록**하는 것이 첫 작업이다. 정의는 `migration_007_ml_seam.sql`을 정본으로 복사한다.
- **실모델 추론 자체**는 슬라이스에서 단위테스트하지 않는다(검증 끝남, 하드닝에서 IO 테스트 부여). 슬라이스의 ML 검증 = DoD의 실사진 e2e 1건.

---

## 8. 완료 기준 (DoD)

1. **macmini 실데이터 e2e 1건**: 작성 페이지에서 실제 손글씨 명세서 사진 업로드 → ml-worker 추론 → `name`(top-5)·`supply` pre-fill → 사람 교정 → confirm → invoice 생성 + `ocr_jobs.invoice_id` 연결 + `ocr_corrections` 1행 적재까지 관통.
2. **백엔드 게이트**: 신규 3종 엔드포인트(`/api/ocr/*`) contract+unit+integration 테스트 통과(동시 confirm claim 시나리오 포함), 커버리지 ≥80%, ruff clean. `tests/fixtures/schema_test.sql` + `conftest.py` TRUNCATE 목록에 `ocr_jobs`·`ocr_corrections` 동기화. `api-spec.json` 갱신.
3. **블로커 해소**: gitignore 재설계 적용, 추론 코어 tracked 이동, 모델 아티팩트가 git에 추적되지 않음 확인. `SJMJ_ML_MODELS_DIR` config 경계 통합.
4. **launchd**: `ai.sjmj.ml-worker` plist 템플릿 + 설치 스크립트, 기동 시 모델 1회 적재 확인.

---

## 9. 구현 도구

응집된 신규 컴포넌트(추론 경계·worker·3 엔드포인트·작성 페이지 증강)이고 트랜잭션 정확성·GT 캡처 안정성이 핵심이라 **standard pipeline**(plan → eng-review → TDD 구현 → PR 리뷰 → merge)이 적합하다. S1(gitignore·파일 이동)·S3 launchd는 직접 + 수동 검증.

---

> **요약**: 검증된 `process_one`을 최소 래핑해 ml-worker(단일 프로세스 device-분리, ocr_jobs DB 큐) 뒤에 두고, 작성 페이지에 사진 업로드 + top-5 pre-fill을 붙여 `업로드→추론→초안→검수→확정` 한 줄기를 macmini 실데이터로 관통시킨다. 어댑터 전면 승격·재학습 자동화·bank 확장은 의도적으로 하드닝으로 미룬다. 결정 근거는 ADR 0001~0003.
