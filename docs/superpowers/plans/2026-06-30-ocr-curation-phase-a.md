# OCR 큐레이션 Phase A — 데이터 척추 + API 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 HITL 교정(`ocr_corrections`)을 행-단위 학습 read-model(`training_pairs`)로 머티리얼라이즈하고, 그 위에 큐레이션 백엔드 슬라이스(6 엔드포인트) + 첫 Pydantic 검증 인프라 + worker 워프 이미지 저장을 얹어 pytest로 완결 검증한다.

**Architecture:** 기존 `router → service → repository` 3계층을 그대로 따라 `curation` 슬라이스를 신설한다. `training_pairs`는 confirm 시점(라이브)과 마이그레이션(백필) 두 경로로 채워지는 머티리얼라이즈 read-model이다. 이미지 2종은 `FileResponse` raw 바이트로 envelope 예외 처리한다. 이 슬라이스는 레포 최초의 Pydantic 검증 슬라이스이므로 `RequestValidationError`→400 envelope 핸들러를 선결로 도입한다.

**Tech Stack:** FastAPI(sync def + threadpool) · SQLAlchemy 2.0 Core raw `text()` · MySQL 8(JSON_TABLE 백필) · Pydantic v2 · pytest(실 MySQL `sjmj_test`).

## Global Constraints

모든 task의 요구사항에 아래가 암묵적으로 포함된다. spec(`docs/superpowers/specs/2026-06-30-ocr-curation-retraining-design.md`)·`AGENTS.md`·`.claude/rules/api-conventions.md`에서 그대로 옮겼다.

- **외부 계약 불변식(절대 보존):** 성공 envelope `{success, data, pagination?}` · 에러 envelope `{success, error:{code, message, details?}}` · 에러코드 체계 `VALIDATION_ERROR`/`NOT_FOUND`/`DUPLICATE_NAME`/`CONFLICT`/`SERVER_ERROR` · 검증 실패 HTTP status **400** · `details` 형태 `{필드: 메시지}` 문자열 맵.
- **이미지 2종(`image/{kind}`·`crop/{row}`)은 success envelope의 명시적 예외** — `FileResponse`로 raw `image/png`를 반환. 에러(404 등)는 여전히 에러 envelope.
- **3계층 경계:** 라우터 핸들러는 `sync def`(FastAPI threadpool). HTTP 관심사(쿼리 파싱·검증·envelope·status)는 라우터, 비즈니스 로직+트랜잭션 경계는 서비스(`with db.transaction():`), raw SQL은 **repository에서만**.
- **API 라우터는 SPA catch-all(`main._mount_static`)보다 먼저 등록**돼야 우선 매칭된다.
- **경로/DB명은 env에서만:** `SJMJ_DATA_DIR`(데이터 루트)·`DB_*`(접속). 절대경로·DB명 하드코딩 금지.
- **스키마 변경은 3곳 동기:** 운영 `db/migration_008_*.sql` + 테스트 `tests/fixtures/schema_test.sql` + `tests/conftest.py:_ALL_TABLES`.
- **api-spec.json 동기 필수:** `.claude/ai-context/api-spec.json`(엔드포인트 SSoT)의 `paths`·`components.schemas`·`x-api-overview.endpoints`를 함께 갱신.
- **ruff 게이트:** google docstring(D) 컨벤션. FastAPI `Body`/`File`/`Depends`는 B008 면제(`pyproject.toml`에 이미 설정). `tests/**`는 D 면제. 작업 후 `uv run ruff check . && uv run ruff format --check .` 통과.
- **커버리지 80% 게이트:** `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80` 통과.
- **테스트 DB:** 실 MySQL `sjmj_test`. `db_conn` 픽스처가 테스트 엔진 주입 + TRUNCATE 격리, 그 엔진 값을 yield한다. `client` 픽스처는 `db_conn`에 의존하는 `TestClient`.
- **Pydantic v2** (`model_validator(mode="after")`, `Literal`, `Field(max_length=...)`).
- **라인 번호는 참고용:** plan의 라인 번호는 ±1 드리프트할 수 있다. 삽입/수정 위치는 함수명·주석·기존 코드 문자열 등 **앵커**로 잡는다(예: "`_unhandled_handler` 아래", "`list_jobs` 다음").

작업 디렉터리: `apps/invoice-ocr/backend` (별도 명시 없으면 모든 backend 경로의 기준). 명령은 이 디렉터리에서 `uv run ...`으로 실행한다.

---

## File Structure

신규/수정 파일과 각 책임:

- `db/migration_008_curation_training_pairs.sql` — **신규(운영)**. `training_pairs` CREATE + `ocr_jobs.curation_reviewed` ALTER + `ocr_corrections`→`training_pairs` 1회 백필(JSON_TABLE).
- `apps/invoice-ocr/backend/tests/fixtures/schema_test.sql` — **수정**. `training_pairs` CREATE + `ocr_jobs`에 `curation_reviewed` 컬럼. (백필 SQL은 운영 전용 — fixture 미포함.)
- `apps/invoice-ocr/backend/tests/conftest.py` — **수정**. `_ALL_TABLES`에 `training_pairs` 추가.
- `apps/invoice-ocr/backend/app/core/errors.py` — **수정**. `RequestValidationError`→400 envelope 핸들러 + 등록.
- `apps/invoice-ocr/backend/app/services/ocr_correction.py` — **수정**. `build_training_pairs` 순수함수 추가.
- `apps/invoice-ocr/backend/app/services/ocr_service.py` — **수정**. confirm 트랜잭션에서 `training_pairs` 머티리얼라이즈.
- `apps/invoice-ocr/backend/app/repositories/curation_repository.py` — **신규**. `training_pairs`·`ocr_jobs`(큐레이션 관점) 데이터 접근 단일 소유자.
- `apps/invoice-ocr/backend/app/services/curation_service.py` — **신규**. 큐레이션 비즈니스 로직 + 이미지 경로 해석.
- `apps/invoice-ocr/backend/app/schemas/curation.py` — **신규**. Pydantic 요청 모델(`CurationPairPatch`).
- `apps/invoice-ocr/backend/app/schemas/__init__.py` — **신규**(빈 패키지).
- `apps/invoice-ocr/backend/app/routers/curation.py` — **신규**. 6 엔드포인트.
- `apps/invoice-ocr/backend/app/main.py` — **수정**. `curation.router` 등록(catch-all 전).
- `apps/invoice-ocr/ml/handwriting/infer_job.py` — **수정**. 워프 전표 `w`를 `crop_out_dir/warped.png`로 저장.
- `.claude/ai-context/api-spec.json` — **수정**. paths/schemas/overview 동기.
- `.claude/rules/api-conventions.md` — **수정**. 이미지 raw-file envelope 예외 명문화.
- 테스트: `tests/unit/test_ocr_correction.py`(수정), `tests/integration/test_ocr_service.py`(수정), `tests/unit/test_errors_validation.py`(신규), `tests/integration/test_curation_schema.py`(신규), `tests/integration/test_curation_repository.py`(신규), `tests/contract/test_curation_routes.py`(신규).

---

## Task 1: Pydantic `RequestValidationError` → 400 envelope 핸들러

큐레이션 PATCH가 첫 Pydantic 슬라이스다. Pydantic 기본 검증 실패는 422 — 외부 계약 불변식인 400 `VALIDATION_ERROR` envelope로 변환하는 전역 핸들러를 선결로 도입한다.

**Files:**

- Modify: `app/core/errors.py` (현재 `register_error_handlers`는 `AppError`+`Exception`만 등록 — `errors.py:52-55`)
- Test: `tests/unit/test_errors_validation.py` (신규)

**Interfaces:**

- Produces: `app/core/errors.py`에 `_validation_error_handler(request, exc: RequestValidationError) -> JSONResponse`를 추가하고 `register_error_handlers`에 등록. 변환 결과 = status 400 + `{"success": false, "error": {"code": "VALIDATION_ERROR", "message": "검증에 실패했습니다.", "details": {필드: 메시지}}}`. `details` 키는 각 에러 `loc`의 마지막 요소(`str`).
  단, `model_validator(mode="after")`처럼 특정 필드에 매이지 않는 whole-object 검증 실패는 Pydantic이 단일 `loc=("body",)`를 산출하므로 details 키가 `"body"`가 된다(필드명 아님). 이는 **의도된 폼-레벨 에러 키 규약**으로 고정한다 — 합성 키(`"_"`/`"non_field"`)로 정규화하지 않는다. 이 동작은 Step 1 단위테스트로 고정한다.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_errors_validation.py`:

```python
"""RequestValidationError → 400 VALIDATION_ERROR envelope 변환 핸들러 단위 검증."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from app.core.errors import register_error_handlers


class _Body(BaseModel):
    name: str


def _app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/echo")
    def echo(body: _Body):  # noqa: D103
        return {"ok": body.name}

    return TestClient(app)


def test_pydantic_validation_failure_becomes_400_envelope():
    res = _app().post("/echo", json={})  # name 누락 → 기본 422
    assert res.status_code == 400
    payload = res.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "name" in payload["error"]["details"]


def test_validation_details_key_is_field_name():
    res = _app().post("/echo", json={"name": 123})  # 타입 불일치
    body = res.json()
    assert body["error"]["details"]["name"]  # 비어있지 않은 메시지


class _ModelBody(BaseModel):
    a: str | None = None

    @model_validator(mode="after")
    def _need_a(self):
        if self.a is None:
            raise ValueError("a가 필요합니다.")
        return self


def _model_app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/m")
    def m(body: _ModelBody):  # noqa: D103
        return {"ok": True}

    return TestClient(app)


def test_model_level_validation_keys_under_body():
    res = _model_app().post("/m", json={})  # model_validator 실패 → loc=("body",)
    assert res.status_code == 400
    details = res.json()["error"]["details"]
    assert "body" in details  # whole-object 에러는 "body" 키로 고정
    assert details["body"]  # 비어있지 않은 메시지
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_errors_validation.py -v`
Expected: FAIL — 기본 FastAPI 422 반환(핸들러 미등록), `assert res.status_code == 400`에서 실패.

- [ ] **Step 3: Write minimal implementation**

`app/core/errors.py` 상단 import에 추가:

```python
from fastapi.exceptions import RequestValidationError
```

`_unhandled_handler` 아래, `register_error_handlers` 위에 추가:

```python
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: dict = {}
    for err in exc.errors():
        loc = err.get("loc") or ("body",)
        field = str(loc[-1])
        details.setdefault(field, err.get("msg", "유효하지 않은 값입니다."))
    return JSONResponse(
        status_code=400,
        content=_error_body("VALIDATION_ERROR", "검증에 실패했습니다.", details),
    )
```

`register_error_handlers` 본문에 등록 한 줄 추가:

```python
def register_error_handlers(app) -> None:
    """앱에 AppError·검증 실패·미처리 예외 핸들러를 등록한다."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_errors_validation.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/core/errors.py apps/invoice-ocr/backend/tests/unit/test_errors_validation.py
git commit -m "feat(backend): Pydantic RequestValidationError를 400 VALIDATION_ERROR envelope로 변환"
```

---

## Task 2: 스키마 — `training_pairs` 테이블 + `ocr_jobs.curation_reviewed` (운영 migration + 테스트 하니스)

spec §3.1·§3.2·§3.4. 스키마를 3곳에 동기한다. 백필 SQL은 운영 전용(JSON_TABLE)이라 fixture에는 넣지 않는다 — 테스트는 confirm 머티리얼라이즈 경로(Task 4)로 데이터를 만든다.

**Files:**

- Create: `db/migration_008_curation_training_pairs.sql`
- Modify: `apps/invoice-ocr/backend/tests/fixtures/schema_test.sql` (ocr_jobs CREATE = 현재 146-158행, ocr_corrections CREATE = 현재 160-171행; 상단 DROP은 6-7행 부근)
- Modify: `apps/invoice-ocr/backend/tests/conftest.py` (`_ALL_TABLES` = 19-30행)
- Test: `tests/integration/test_curation_schema.py` (신규)

**Interfaces:**

- Produces: 테스트 DB에 `training_pairs` 테이블(컬럼: `id, crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status, reviewed_at, created_at`) + `ocr_jobs.curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE`. `crop_ref` UNIQUE. 후속 task의 repository가 이 스키마에 의존한다.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_curation_schema.py`:

```python
"""training_pairs 스키마 + ocr_jobs.curation_reviewed가 테스트 하니스에 반영됐는지 검증."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


def test_training_pairs_insert_and_readback(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/x.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, final_label, canonical_label, supply, status) "
                "VALUES (:r, :j, 0, '삼겹살', '목살', '목살', 120000, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
        row = conn.execute(
            text("SELECT crop_ref, status, canonical_label, reviewed_at FROM training_pairs WHERE job_id = :j"),
            {"j": job_id},
        ).mappings().first()
    assert row["crop_ref"] == f"job-{job_id}/row-0"
    assert row["status"] == "included"
    assert row["canonical_label"] == "목살"
    assert row["reviewed_at"] is None


def test_ocr_jobs_curation_reviewed_defaults_false(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/y.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        reviewed = conn.execute(
            text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert reviewed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_curation_schema.py -v`
Expected: FAIL — `training_pairs` 테이블·`curation_reviewed` 컬럼 미존재(ProgrammingError: Unknown column / table doesn't exist).

- [ ] **Step 3: Write minimal implementation**

3-1. `db/migration_008_curation_training_pairs.sql` 생성:

```sql
-- Migration 008: 큐레이션 게이트 + training_pairs read-model
-- 적용 순서: 007 → 008.
-- 목적: 라이브 교정(ocr_corrections)을 행-단위 학습 read-model(training_pairs)로 머티리얼라이즈,
--       ocr_jobs에 잡-단위 검수 게이트(curation_reviewed) 추가, 기존 교정 1회 백필.
-- ROLLBACK:
--   DROP TABLE IF EXISTS training_pairs;
--   ALTER TABLE ocr_jobs DROP COLUMN curation_reviewed;

-- 1) training_pairs (confirm된 행마다 1행, crop_ref 전역 유니크)
CREATE TABLE IF NOT EXISTS training_pairs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    crop_ref VARCHAR(64) UNIQUE NOT NULL,            -- "job-42/row-0"
    job_id INT UNSIGNED NOT NULL,
    invoice_id INT,
    row_index INT NOT NULL,
    draft_label VARCHAR(200),                        -- 모델 top-1 (item_top5[0].label)
    final_label VARCHAR(200),                        -- confirm 시 사용자 입력명 (불변 스냅샷)
    canonical_label VARCHAR(200),                    -- 학습용 정규화 라벨 (기본 = final_label)
    supply INT,                                      -- 행 식별용 읽기전용 맥락
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

-- 2) ocr_jobs 잡-단위 검수 게이트 (재실행 안전 가드)
SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_jobs'
    AND column_name = 'curation_reviewed'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE ocr_jobs ADD COLUMN curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3) 기존 ocr_corrections.correction_json.lines[] → training_pairs 1회 백필
--    crop_ref UNIQUE + ON DUPLICATE KEY no-op 으로 재실행 멱등.
INSERT INTO training_pairs
    (crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status)
SELECT
    jt.crop_ref,
    c.job_id,
    c.invoice_id,
    CAST(SUBSTRING_INDEX(jt.crop_ref, '/row-', -1) AS UNSIGNED),
    jt.draft_label,
    jt.final_label,
    jt.final_label,
    jt.final_supply,
    'included'
FROM ocr_corrections c
JOIN JSON_TABLE(
    c.correction_json, '$.lines[*]' COLUMNS (
        crop_ref VARCHAR(64) PATH '$.crop_ref',
        draft_label VARCHAR(200) PATH '$.draft_label',
        final_label VARCHAR(200) PATH '$.final_label',
        final_supply INT PATH '$.final_supply'
    )
) AS jt
WHERE jt.crop_ref IS NOT NULL
  AND c.job_id IS NOT NULL
ON DUPLICATE KEY UPDATE training_pairs.id = training_pairs.id;
```

3-2. `tests/fixtures/schema_test.sql` — 상단 DROP 블록(`DROP TABLE IF EXISTS ocr_corrections;` `DROP TABLE IF EXISTS ocr_jobs;`가 있는 곳, 현재 6-7행)에서 `ocr_corrections` DROP **앞**에 `training_pairs` DROP을 추가(FK 의존 역순):

```sql
DROP TABLE IF EXISTS training_pairs;
DROP TABLE IF EXISTS ocr_corrections;
DROP TABLE IF EXISTS ocr_jobs;
```

3-3. `tests/fixtures/schema_test.sql` — `CREATE TABLE ocr_jobs (...)` 블록의 `invoice_id INT,` 줄 바로 다음에 컬럼 추가:

```sql
    invoice_id INT,
    curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
```

3-4. `tests/fixtures/schema_test.sql` — `ocr_corrections` CREATE 블록 **다음**에 `training_pairs` CREATE 추가(운영 §3.1과 동일 컬럼, 백필 SQL 제외):

```sql
-- 학습 read-model 테이블 (migration_008)
CREATE TABLE training_pairs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    crop_ref VARCHAR(64) UNIQUE NOT NULL,
    job_id INT UNSIGNED NOT NULL,
    invoice_id INT,
    row_index INT NOT NULL,
    draft_label VARCHAR(200),
    final_label VARCHAR(200),
    canonical_label VARCHAR(200),
    supply INT,
    status VARCHAR(16) NOT NULL DEFAULT 'included',
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

3-5. `tests/conftest.py` — `_ALL_TABLES` 리스트 첫 항목으로 `"training_pairs"` 추가(FK 자식이 먼저 TRUNCATE되도록; 어차피 `SET FOREIGN_KEY_CHECKS=0`이지만 가독성):

```python
_ALL_TABLES = [
    "training_pairs",
    "ocr_corrections",
    "ocr_jobs",
    ...
]
```

- [ ] **Step 4: Run test to verify it passes**

세션 스키마는 `_engine` 픽스처(session scope)가 새로 만든다. 재실행:

Run: `uv run pytest tests/integration/test_curation_schema.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add db/migration_008_curation_training_pairs.sql \
        apps/invoice-ocr/backend/tests/fixtures/schema_test.sql \
        apps/invoice-ocr/backend/tests/conftest.py \
        apps/invoice-ocr/backend/tests/integration/test_curation_schema.py
git commit -m "feat(db): training_pairs read-model + ocr_jobs.curation_reviewed (migration_008 + 테스트 하니스)"
```

> **운영 백필 검증(테스트 아님, macmini 적용 시 수동):** migration_008 적용 후
> `SELECT COUNT(*) FROM training_pairs;` 가 `ocr_corrections`의 `lines[].crop_ref` 총합과 일치하는지 1회 확인.

---

## Task 3: `build_training_pairs` 순수함수

confirm이 계산한 correction `lines[]`를 `training_pairs` insert dict 리스트로 변환하는 순수함수. crop_ref 있는 행(= 매칭된 초안 행)만 학습 후보가 된다.

**Files:**

- Modify: `app/services/ocr_correction.py` (현재 `build_correction`만 — `ocr_correction.py:4-40`)
- Test: `tests/unit/test_ocr_correction.py` (수정 — 기존 `build_correction` 테스트 보존)

**Interfaces:**

- Consumes: `build_correction` 반환 dict의 `lines[]` 항목 형태 `{crop_ref, draft_label, final_label, label_changed, draft_supply, final_supply, supply_changed}` (`ocr_correction.py:25-34` 참조).
- Produces: `build_training_pairs(job_id: int, invoice_id: int, correction: dict) -> list[dict]`. 각 dict 키 = `crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status`. `canonical_label` 초기값 = `final_label`, `supply` = `final_supply`, `status` = `"included"`, `row_index` = `crop_ref`의 `/row-` 뒤 정수. `crop_ref` 없는 line은 제외.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_ocr_correction.py` 끝에 추가(상단 import는 `from app.services.ocr_correction import build_correction` → `build_training_pairs`도 추가):

```python
from app.services.ocr_correction import build_training_pairs


def _correction(lines):
    return {"lines": lines, "rows_added": 0, "rows_dropped": 0}


def test_build_training_pairs_maps_line_to_pair():
    correction = _correction(
        [
            {
                "crop_ref": "job-42/row-0",
                "draft_label": "삼겹살",
                "final_label": "목살",
                "label_changed": True,
                "draft_supply": 120000,
                "final_supply": 120000,
                "supply_changed": False,
            }
        ]
    )
    pairs = build_training_pairs(42, 7, correction)
    assert pairs == [
        {
            "crop_ref": "job-42/row-0",
            "job_id": 42,
            "invoice_id": 7,
            "row_index": 0,
            "draft_label": "삼겹살",
            "final_label": "목살",
            "canonical_label": "목살",
            "supply": 120000,
            "status": "included",
        }
    ]


def test_build_training_pairs_skips_lines_without_crop_ref():
    correction = _correction([{"final_label": "수기품목", "final_supply": 5000}])
    assert build_training_pairs(1, 1, correction) == []


def test_build_training_pairs_parses_multidigit_row_index():
    correction = _correction(
        [{"crop_ref": "job-9/row-12", "draft_label": None, "final_label": "X", "final_supply": None}]
    )
    pair = build_training_pairs(9, 3, correction)[0]
    assert pair["row_index"] == 12
    assert pair["canonical_label"] == "X"
    assert pair["supply"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_ocr_correction.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_training_pairs'`.

- [ ] **Step 3: Write minimal implementation**

`app/services/ocr_correction.py` 끝에 추가:

```python
def build_training_pairs(job_id: int, invoice_id: int, correction: dict) -> list[dict]:
    """correction lines[]를 training_pairs insert dict 리스트로 변환한다.

    crop_ref 있는 행(매칭된 초안 행)만 학습 후보다. canonical_label 초기값은
    final_label, status는 included.

    Args:
        job_id: 확정된 OCR 잡 id.
        invoice_id: confirm으로 생성된 invoice id.
        correction: build_correction 반환 dict (lines[] 보유).

    Returns:
        training_pairs insert용 dict 리스트(crop_ref 없는 line 제외).
    """
    pairs: list[dict] = []
    for line in correction.get("lines", []):
        ref = line.get("crop_ref")
        if not ref:
            continue
        final_label = line.get("final_label")
        pairs.append(
            {
                "crop_ref": ref,
                "job_id": job_id,
                "invoice_id": invoice_id,
                "row_index": int(ref.rsplit("/row-", 1)[-1]),
                "draft_label": line.get("draft_label"),
                "final_label": final_label,
                "canonical_label": final_label,
                "supply": line.get("final_supply"),
                "status": "included",
            }
        )
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_ocr_correction.py -v`
Expected: PASS (기존 4 + 신규 3 = 7 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/services/ocr_correction.py apps/invoice-ocr/backend/tests/unit/test_ocr_correction.py
git commit -m "feat(backend): build_training_pairs 순수함수 — correction lines를 학습쌍으로 변환"
```

---

## Task 4: confirm 머티리얼라이즈 — `CurationRepository.insert_training_pairs` + `OcrService.confirm` 배선

신규 confirm이 같은 트랜잭션 안에서 `training_pairs`를 라인별 insert한다. `training_pairs` 테이블의 단일 소유 repository를 신설하고 `OcrService`에 주입한다.

**Files:**

- Create: `app/repositories/curation_repository.py`
- Modify: `app/services/ocr_service.py` (`__init__` = 28-34행, `confirm` = 57-88행)
- Test: `tests/integration/test_ocr_service.py` (수정 — 신규 테스트 추가)

**Interfaces:**

- Consumes: `build_training_pairs(job_id, invoice_id, correction)` (Task 3). `db.connection()`(현재 conn 재사용; confirm 트랜잭션에 합류).
- Produces: `CurationRepository.insert_training_pairs(pairs: list[dict]) -> int` (insert 행 수 반환, 빈 리스트면 0). `OcrService.__init__(self, repo=None, invoice_service=None, *, transaction=None, curation_repo=None)` — `curation_repo` 기본값 `CurationRepository()`. confirm이 `insert_correction` 직후 `self.curation_repo.insert_training_pairs(build_training_pairs(...))` 호출.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_ocr_service.py`의 `test_confirm_creates_invoice_links_job_and_logs_correction` 다음에 추가:

```python
def test_confirm_materializes_training_pairs():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(
        job_id,
        "done",
        {
            "rows": [
                {
                    "crop_ref": f"job-{job_id}/row-0",
                    "item_top5": [{"label": "삼겹살", "sim": 0.8}],
                    "supply": 100000,
                }
            ],
            "supply_sum": 100000,
            "warp_ok": True,
        },
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = f"job-{job_id}/row-0"
    payload["items"][0]["name"] = "목살"
    payload["items"][0]["supply"] = 100000

    out = OcrService().confirm(job_id, payload)

    from sqlalchemy import text

    from app.db import connection

    with connection() as conn:
        row = conn.execute(
            text(
                "SELECT crop_ref, draft_label, final_label, canonical_label, supply, status, invoice_id "
                "FROM training_pairs WHERE job_id = :j"
            ),
            {"j": job_id},
        ).mappings().first()
    assert row["crop_ref"] == f"job-{job_id}/row-0"
    assert row["draft_label"] == "삼겹살"
    assert row["final_label"] == "목살"
    assert row["canonical_label"] == "목살"
    assert row["status"] == "included"
    assert row["invoice_id"] == out["invoice_id"]
```

> 참고: `td.invoice_with_items()` 첫 item에 `crop_ref`/`name`/`supply`를 덮어쓴다. 기존
> `test_confirm_creates_invoice_links_job_and_logs_correction`(41-77행)이 동일 패턴을 쓴다.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ocr_service.py::test_confirm_materializes_training_pairs -v`
Expected: FAIL — confirm이 `training_pairs`를 채우지 않아 `row is None` → `TypeError`/`AssertionError`.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/repositories/curation_repository.py` 생성(이 task 범위는 `insert_training_pairs`만; 이후 task가 같은 파일에 메서드 추가):

```python
"""training_pairs / ocr_jobs(큐레이션 관점) 데이터 접근. text() raw SQL 직접 발행."""

from sqlalchemy import text

from app.db import connection

_PAIR_INSERT = text(
    "INSERT INTO training_pairs "
    "(crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status) "
    "VALUES (:crop_ref, :job_id, :invoice_id, :row_index, :draft_label, :final_label, "
    ":canonical_label, :supply, :status)"
)


class CurationRepository:
    """training_pairs 테이블의 단일 소유 레포지토리(읽기/쓰기)."""

    def insert_training_pairs(self, pairs: list[dict]) -> int:
        """학습쌍 dict 리스트를 라인별 삽입하고 삽입 행 수를 반환한다."""
        if not pairs:
            return 0
        with connection() as conn:
            for pair in pairs:
                conn.execute(_PAIR_INSERT, pair)
        return len(pairs)
```

3-2. `app/services/ocr_service.py` 수정:

import 추가(13행 부근, `from app.services.ocr_correction import build_correction` 옆):

```python
from app.repositories.curation_repository import CurationRepository
from app.services.ocr_correction import build_correction, build_training_pairs
```

`__init__` 시그니처/본문(28-34행) 교체:

```python
    def __init__(self, repo=None, invoice_service=None, *, transaction=None, curation_repo=None):
        """저장소·invoice_service·트랜잭션 seam·큐레이션 저장소를 주입받아 초기화한다."""
        self.repo = repo or OcrRepository()
        self.invoice_service = invoice_service or InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        )
        self._transaction = transaction or db.transaction
        self.curation_repo = curation_repo or CurationRepository()
```

`confirm` 본문에서 `insert_correction` 호출 다음(현재 86행) 두 줄 추가:

```python
            correction = build_correction(job["result_json"] or {}, payload.get("items", []))
            self.repo.insert_correction(job_id, invoice_id, correction)
            pairs = build_training_pairs(job_id, invoice_id, correction)
            self.curation_repo.insert_training_pairs(pairs)
```

> 주의: `test_confirm_rollback_on_race_link_returns_zero`(121-166행)는 `_StubRepo`를 주입하지만
> `curation_repo`는 주입하지 않아 기본 `CurationRepository()`가 쓰인다. 그 테스트는 conflict가
> `insert_correction` **이전**에 발생(link_invoice=0)하므로 `insert_training_pairs`에 도달하지 않는다 —
> 변경 영향 없음(회귀 확인용).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_ocr_service.py -v`
Expected: PASS — 신규 테스트 + 기존 confirm 테스트 전부 통과(회귀 없음).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/ocr_service.py \
        apps/invoice-ocr/backend/tests/integration/test_ocr_service.py
git commit -m "feat(backend): confirm 시 training_pairs 머티리얼라이즈(같은 트랜잭션)"
```

---

## Task 5: `GET /api/curation/jobs` — 검수 큐 목록

confirmed 잡(= `training_pairs` 보유) 목록을 검수상태·미처리수와 함께 페이지네이션 반환. 미검수 잡 우선 정렬. 큐레이션 라우터를 main에 등록한다(catch-all 전).

**Files:**

- Modify: `app/repositories/curation_repository.py` (Task 4 파일에 메서드 추가)
- Create: `app/services/curation_service.py`
- Create: `app/routers/curation.py`
- Modify: `app/main.py` (import 9-17행, include_router 57-63행)
- Test: `tests/contract/test_curation_routes.py` (신규)

**Interfaces:**

- Produces:
  - `CurationRepository.list_jobs(limit: int, offset: int) -> tuple[list[dict], int]` — `(jobs, total)`. 각 job dict = `{job_id, invoice_id, curation_reviewed(int 0/1), pair_count(int), unreviewed_count(int), created_at}`. total = `training_pairs` 보유 잡 수.
  - `CurationService.list_jobs(page: int, limit: int) -> tuple[list[dict], int]` — repo 호출 + `curation_reviewed`를 `bool`, count류를 `int`로 정규화.
  - 라우터 `GET /api/curation/jobs?page=&limit=` → `envelope.list_response`.

- [ ] **Step 1: Write the failing test**

`tests/contract/test_curation_routes.py`:

```python
"""curation 슬라이스 계약 테스트 — 검수 큐 목록."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job_with_pairs(engine, *, reviewed=0, pairs=2, unreviewed=2):
    """ocr_jobs 1건 + training_pairs N건 시드. job_id 반환."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_jobs (status, image_path, curation_reviewed) VALUES ('done', '/x.jpg', :r)"),
            {"r": reviewed},
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        for i in range(pairs):
            stamped = "NULL" if i < unreviewed else "CURRENT_TIMESTAMP"
            conn.execute(
                text(
                    "INSERT INTO training_pairs "
                    "(crop_ref, job_id, row_index, final_label, canonical_label, supply, status, reviewed_at) "
                    f"VALUES (:r, :j, :i, '품목', '품목', 1000, 'included', {stamped})"
                ),
                {"r": f"job-{job_id}/row-{i}", "j": job_id, "i": i},
            )
    return job_id


def test_list_jobs_returns_queue_with_counts(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=3, unreviewed=2)
    res = client.get("/api/curation/jobs")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert "pagination" in body
    job = next(j for j in body["data"] if j["job_id"] == job_id)
    assert job["pair_count"] == 3
    assert job["unreviewed_count"] == 2
    assert job["curation_reviewed"] is False


def test_list_jobs_excludes_jobs_without_pairs(client, db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/no-pairs.jpg')"))
        empty_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    res = client.get("/api/curation/jobs")
    assert all(j["job_id"] != empty_id for j in res.json()["data"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: FAIL — `/api/curation/jobs` 미등록 → SPA fallback이 `index.html`(또는 404) 반환, 200 JSON 아님.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/repositories/curation_repository.py`에 메서드 추가(클래스 내부):

```python
    def list_jobs(self, limit: int, offset: int) -> tuple[list[dict], int]:
        """training_pairs 보유 잡을 검수상태·미처리수와 함께 페이지 조회한다."""
        list_sql = text(
            "SELECT j.id AS job_id, j.invoice_id, j.curation_reviewed, j.created_at, "
            "COUNT(tp.id) AS pair_count, "
            "SUM(CASE WHEN tp.reviewed_at IS NULL THEN 1 ELSE 0 END) AS unreviewed_count "
            "FROM ocr_jobs j JOIN training_pairs tp ON tp.job_id = j.id "
            "GROUP BY j.id, j.invoice_id, j.curation_reviewed, j.created_at "
            "ORDER BY j.curation_reviewed ASC, j.created_at DESC, j.id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        count_sql = text("SELECT COUNT(DISTINCT job_id) FROM training_pairs")
        with connection() as conn:
            rows = conn.execute(list_sql, {"limit": limit, "offset": offset}).mappings().all()
            total = conn.execute(count_sql).scalar() or 0
        return [dict(r) for r in rows], int(total)
```

3-2. `app/services/curation_service.py` 생성:

```python
"""CurationService — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지 경로 해석.

라우터(HTTP)와 repository(SQL) 사이의 정규화·비즈니스 로직 계층.
"""

from app.repositories.curation_repository import CurationRepository


class CurationService:
    """큐레이션 도메인 서비스."""

    def __init__(self, repo=None):
        """저장소를 주입받아 초기화한다(미지정 시 기본 구현)."""
        self.repo = repo or CurationRepository()

    def list_jobs(self, page: int, limit: int) -> tuple[list[dict], int]:
        """검수 큐(페이지)를 조회하고 표시용 타입으로 정규화한다."""
        offset = (page - 1) * limit
        rows, total = self.repo.list_jobs(limit, offset)
        jobs = [
            {
                "job_id": int(r["job_id"]),
                "invoice_id": r["invoice_id"],
                "curation_reviewed": bool(r["curation_reviewed"]),
                "pair_count": int(r["pair_count"]),
                "unreviewed_count": int(r["unreviewed_count"] or 0),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return jobs, total
```

3-3. `app/routers/curation.py` 생성:

```python
"""curation 라우터 — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지. /api/curation/*.

이미지 2종(image/{kind}·crop/{row})은 FileResponse raw 바이트로 success envelope의
명시적 예외(api-conventions.md 참조). 그 외는 표준 envelope.
"""

from fastapi import APIRouter

from app.core import envelope
from app.services.curation_service import CurationService

router = APIRouter()

_LIMIT_MAX = 100


def _service() -> CurationService:
    return CurationService()


@router.get("/curation/jobs")
def list_jobs(page: int = 1, limit: int = 20):
    """검수 큐(confirmed 잡) 목록을 페이지 조회한다."""
    page = max(1, page)
    limit = max(1, min(_LIMIT_MAX, limit))
    jobs, total = _service().list_jobs(page, limit)
    total_pages = (total + limit - 1) // limit if total else 1
    return envelope.list_response(
        jobs, {"page": page, "limit": limit, "total": total, "totalPages": total_pages}
    )
```

3-4. `app/main.py` — import 튜플(9-17행)에 `curation` 추가, include_router(57-63행)에 한 줄 추가(catch-all `_mount_static`는 64행이라 그 전):

```python
from app.routers import (
    companies,
    curation,
    invoices,
    items,
    ocr,
    sales_records,
    salespeople,
    settings,
)
```

```python
    application.include_router(invoices.router, prefix="/api")
    application.include_router(ocr.router, prefix="/api")
    application.include_router(curation.router, prefix="/api")
    application.include_router(companies.router, prefix="/api")
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/curation_service.py \
        apps/invoice-ocr/backend/app/routers/curation.py \
        apps/invoice-ocr/backend/app/main.py \
        apps/invoice-ocr/backend/tests/contract/test_curation_routes.py
git commit -m "feat(backend): GET /api/curation/jobs 검수 큐 목록 슬라이스"
```

---

## Task 6: `GET /api/curation/jobs/{job_id}` — 잡 상세(행별 top5 포함)

잡 1건의 단계 이미지 신호(`warp_ok`) + 행별 쌍(crop_ref·top5·draft/final/canonical·supply·status)을 반환. top5는 `ocr_jobs.result_json`의 행에서 조인한다.

**Files:**

- Modify: `app/repositories/curation_repository.py`
- Modify: `app/services/curation_service.py`
- Modify: `app/routers/curation.py`
- Test: `tests/contract/test_curation_routes.py` (수정)

**Interfaces:**

- Consumes: `ocr_jobs.result_json`의 `rows[]` 항목 `{row_index, crop_ref, item_top5, supply, ...}` (`infer_job.assemble_result_json` 산물).
- Produces:
  - `CurationRepository.find_job_detail(job_id: int) -> dict | None` — `{"job": {id, invoice_id, curation_reviewed, result_json(parsed dict|None), created_at}, "pairs": [training_pairs 행...]}`. 없으면 None.
  - `CurationService.get_detail(job_id: int) -> dict` — 없으면 `not_found`. 반환 `{job_id, invoice_id, curation_reviewed(bool), warp_ok(bool), created_at, pairs:[{id, crop_ref, row_index, draft_label, final_label, canonical_label, supply, status, reviewed_at, top5:list}]}`. top5 = result_json `rows[]`에서 `row_index`로 매칭한 `item_top5`(없으면 `[]`).
  - 라우터 `GET /api/curation/jobs/{job_id}` → `envelope.single`.

- [ ] **Step 1: Write the failing test**

`tests/contract/test_curation_routes.py`에 추가:

```python
def test_job_detail_includes_pairs_with_top5(client, db_conn):
    with db_conn.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json) "
                "VALUES ('done', '/x.jpg', :rj)"
            ),
            {
                "rj": (
                    '{"rows": [{"row_index": 0, "crop_ref": "job-1/row-0", '
                    '"item_top5": [{"label": "삼겹살", "sim": 0.8}], "supply": 100000}], '
                    '"supply_sum": 100000, "warp_ok": true}'
                )
            },
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, final_label, canonical_label, supply, status) "
                "VALUES (:r, :j, 0, '삼겹살', '목살', '목살', 100000, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
    res = client.get(f"/api/curation/jobs/{job_id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["job_id"] == job_id
    assert data["warp_ok"] is True
    pair = data["pairs"][0]
    assert pair["canonical_label"] == "목살"
    assert pair["draft_label"] == "삼겹살"
    assert pair["top5"][0]["label"] == "삼겹살"


def test_job_detail_404_when_missing(client, db_conn):
    res = client.get("/api/curation/jobs/999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_curation_routes.py -k job_detail -v`
Expected: FAIL — 라우트 미등록.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/repositories/curation_repository.py` — 상단에 json import 추가 + 메서드:

```python
import json
```

```python
    def find_job_detail(self, job_id: int) -> dict | None:
        """잡 1건 + training_pairs(행순)를 함께 조회한다(result_json 파싱 포함)."""
        with connection() as conn:
            job_row = conn.execute(
                text(
                    "SELECT id, invoice_id, curation_reviewed, result_json, created_at "
                    "FROM ocr_jobs WHERE id = :id"
                ),
                {"id": job_id},
            ).mappings().first()
            if job_row is None:
                return None
            pair_rows = conn.execute(
                text(
                    "SELECT id, crop_ref, row_index, draft_label, final_label, canonical_label, "
                    "supply, status, reviewed_at FROM training_pairs "
                    "WHERE job_id = :id ORDER BY row_index ASC, id ASC"
                ),
                {"id": job_id},
            ).mappings().all()
        # result_json 파싱은 ocr_repository._parse_job와 동일 관용구 — repo 격리상 의도적 중복(공유 추출 안 함).
        job = dict(job_row)
        raw = job.get("result_json")
        job["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
        return {"job": job, "pairs": [dict(p) for p in pair_rows]}
```

3-2. `app/services/curation_service.py` — import + 메서드:

```python
from app.core.errors import not_found
```

```python
    def get_detail(self, job_id: int) -> dict:
        """잡 상세(행별 top5 조인 포함)를 조회한다. 없으면 404."""
        detail = self.repo.find_job_detail(job_id)
        if detail is None:
            not_found("OCR 잡을 찾을 수 없습니다.")
        job = detail["job"]
        result = job.get("result_json") or {}
        top5_by_row = {
            r.get("row_index"): (r.get("item_top5") or []) for r in result.get("rows", [])
        }
        pairs = [
            {
                "id": int(p["id"]),
                "crop_ref": p["crop_ref"],
                "row_index": int(p["row_index"]),
                "draft_label": p["draft_label"],
                "final_label": p["final_label"],
                "canonical_label": p["canonical_label"],
                "supply": p["supply"],
                "status": p["status"],
                "reviewed_at": p["reviewed_at"],
                "top5": top5_by_row.get(int(p["row_index"]), []),
            }
            for p in detail["pairs"]
        ]
        return {
            "job_id": int(job["id"]),
            "invoice_id": job["invoice_id"],
            "curation_reviewed": bool(job["curation_reviewed"]),
            "warp_ok": bool(result.get("warp_ok", False)),
            "created_at": job["created_at"],
            "pairs": pairs,
        }
```

3-3. `app/routers/curation.py` — 라우트 추가(list_jobs 다음):

```python
@router.get("/curation/jobs/{job_id}")
def job_detail(job_id: int):
    """잡 상세(단계 이미지 신호 + 행별 쌍)를 조회한다."""
    return envelope.single(_service().get_detail(job_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/curation_service.py \
        apps/invoice-ocr/backend/app/routers/curation.py \
        apps/invoice-ocr/backend/tests/contract/test_curation_routes.py
git commit -m "feat(backend): GET /api/curation/jobs/{id} 잡 상세(행별 top5 조인)"
```

---

## Task 7: `PATCH /api/curation/pairs/{id}` — 쌍 큐레이션(첫 Pydantic 슬라이스)

`status`(included|excluded) 또는 `canonical_label`을 갱신. Pydantic 요청 모델로 검증하고 Task 1 핸들러가 422→400 envelope 변환을 보장한다.

**Files:**

- Create: `app/schemas/__init__.py` (빈 파일)
- Create: `app/schemas/curation.py`
- Modify: `app/repositories/curation_repository.py`
- Modify: `app/services/curation_service.py`
- Modify: `app/routers/curation.py`
- Test: `tests/contract/test_curation_routes.py` (수정)

**Interfaces:**

- Produces:
  - `CurationPairPatch(BaseModel)` — `status: Literal["included","excluded"] | None = None`, `canonical_label: str | None = Field(default=None, min_length=1, max_length=200)`. `model_validator(mode="after")`로 둘 다 None이면 `ValueError`.
  - `CurationRepository.find_pair(pair_id: int) -> dict | None`, `CurationRepository.update_pair(pair_id: int, fields: dict) -> None` (화이트리스트 `status`/`canonical_label`만 SET).
  - `CurationService.patch_pair(pair_id: int, fields: dict) -> dict` — 없으면 `not_found`, 갱신 후 쌍 반환.
  - 라우터 `PATCH /api/curation/pairs/{id}` body=`CurationPairPatch` → `envelope.single`. `id`=`training_pairs.id` 정수(crop_ref는 슬래시 포함이라 path param 부적합 — spec §4.1).

- [ ] **Step 1: Write the failing test**

`tests/contract/test_curation_routes.py`에 추가(상단 helper 재사용 — `_seed_job_with_pairs`는 단일 라벨 '품목'을 넣음):

```python
def _first_pair_id(engine, job_id):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM training_pairs WHERE job_id = :j ORDER BY id ASC LIMIT 1"),
            {"j": job_id},
        ).scalar()


def test_patch_pair_updates_canonical_label(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "정식명"})
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "정식명"


def test_patch_pair_updates_status(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"status": "excluded"})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "excluded"


def test_patch_pair_empty_body_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "body" in res.json()["error"]["details"]  # model_validator 실패는 "body" 키(계약 고정)


def test_patch_pair_invalid_status_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"status": "garbage"})
    assert res.status_code == 400


def test_patch_pair_404_when_missing(client, db_conn):
    res = client.patch("/api/curation/pairs/999999", json={"status": "excluded"})
    assert res.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_curation_routes.py -k patch_pair -v`
Expected: FAIL — 라우트 미등록.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/schemas/__init__.py` 생성(빈 파일).

3-2. `app/schemas/curation.py` 생성:

```python
"""curation 슬라이스 Pydantic 요청 모델. 레포 최초 Pydantic 슬라이스."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CurationPairPatch(BaseModel):
    """학습쌍 부분 갱신 요청 — status 또는 canonical_label 중 하나 이상."""

    status: Literal["included", "excluded"] | None = None
    canonical_label: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _at_least_one(self) -> "CurationPairPatch":
        if self.status is None and self.canonical_label is None:
            raise ValueError("status 또는 canonical_label 중 하나는 필요합니다.")
        return self
```

3-3. `app/repositories/curation_repository.py` — 메서드 추가:

```python
    def find_pair(self, pair_id: int) -> dict | None:
        """학습쌍을 id로 단건 조회한다."""
        with connection() as conn:
            row = conn.execute(
                text(
                    "SELECT id, crop_ref, job_id, invoice_id, row_index, draft_label, "
                    "final_label, canonical_label, supply, status, reviewed_at, created_at "
                    "FROM training_pairs WHERE id = :id"
                ),
                {"id": pair_id},
            ).mappings().first()
            return dict(row) if row else None

    def update_pair(self, pair_id: int, fields: dict) -> None:
        """학습쌍의 status/canonical_label을 갱신한다(화이트리스트 컬럼만)."""
        allowed = ("status", "canonical_label")
        cols = [c for c in allowed if c in fields]
        # 방어: 라우터는 model_validator로 검증된 비어있지 않은 fields만 전달(API 경로로는 도달 불가).
        if not cols:
            return
        set_clause = ", ".join(f"{c} = :{c}" for c in cols)
        params = {c: fields[c] for c in cols}
        params["id"] = pair_id
        with connection() as conn:
            conn.execute(text(f"UPDATE training_pairs SET {set_clause} WHERE id = :id"), params)
```

3-4. `app/services/curation_service.py` — 메서드 추가:

```python
    def patch_pair(self, pair_id: int, fields: dict) -> dict:
        """학습쌍을 부분 갱신하고 갱신된 쌍을 반환한다. 없으면 404."""
        if self.repo.find_pair(pair_id) is None:
            not_found("학습쌍을 찾을 수 없습니다.")
        self.repo.update_pair(pair_id, fields)
        updated = self.repo.find_pair(pair_id)
        return {
            "id": int(updated["id"]),
            "crop_ref": updated["crop_ref"],
            "job_id": int(updated["job_id"]),
            "row_index": int(updated["row_index"]),
            "draft_label": updated["draft_label"],
            "final_label": updated["final_label"],
            "canonical_label": updated["canonical_label"],
            "supply": updated["supply"],
            "status": updated["status"],
            "reviewed_at": updated["reviewed_at"],
        }
```

3-5. `app/routers/curation.py` — import + 라우트:

```python
from app.schemas.curation import CurationPairPatch
```

```python
@router.patch("/curation/pairs/{id}")
def patch_pair(id: int, patch: CurationPairPatch):
    """학습쌍의 status 또는 canonical_label을 갱신한다."""
    return envelope.single(_service().patch_pair(id, patch.model_dump(exclude_unset=True)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: PASS (전체 큐레이션 계약 통과).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/schemas/ \
        apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/curation_service.py \
        apps/invoice-ocr/backend/app/routers/curation.py \
        apps/invoice-ocr/backend/tests/contract/test_curation_routes.py
git commit -m "feat(backend): PATCH /api/curation/pairs/{id} 쌍 큐레이션(첫 Pydantic 슬라이스)"
```

---

## Task 8: `POST /api/curation/jobs/{job_id}/review` — 잡 검수 완료

잡을 `curation_reviewed=TRUE`로 표시하고 미처리 쌍(`reviewed_at IS NULL`)에 타임스탬프를 찍는다. 멱등(재호출 안전).

**Files:**

- Modify: `app/repositories/curation_repository.py`
- Modify: `app/services/curation_service.py`
- Modify: `app/routers/curation.py`
- Test: `tests/contract/test_curation_routes.py` (수정)

**Interfaces:**

- Produces:
  - `CurationRepository.job_exists(job_id: int) -> bool`.
  - `CurationRepository.mark_reviewed(job_id: int) -> None` — `ocr_jobs.curation_reviewed=1` UPDATE + `training_pairs SET reviewed_at=CURRENT_TIMESTAMP WHERE job_id=:id AND reviewed_at IS NULL`.
  - `CurationService.mark_reviewed(job_id: int) -> dict` — 없으면 `not_found`, 있으면 mark + `{"job_id": job_id, "curation_reviewed": True}` 반환.
  - 라우터 `POST /api/curation/jobs/{job_id}/review` → `envelope.single`.

- [ ] **Step 1: Write the failing test**

`tests/contract/test_curation_routes.py`에 추가:

```python
def test_review_marks_job_and_stamps_unreviewed_pairs(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=2, unreviewed=2)
    res = client.post(f"/api/curation/jobs/{job_id}/review")
    assert res.status_code == 200
    assert res.json()["data"]["curation_reviewed"] is True

    with db_conn.begin() as conn:
        reviewed = conn.execute(
            text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
        unstamped = conn.execute(
            text("SELECT COUNT(*) FROM training_pairs WHERE job_id = :id AND reviewed_at IS NULL"),
            {"id": job_id},
        ).scalar()
    assert reviewed == 1
    assert unstamped == 0


def test_review_is_idempotent(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=1, pairs=1, unreviewed=0)
    res = client.post(f"/api/curation/jobs/{job_id}/review")
    assert res.status_code == 200


def test_review_404_when_missing(client, db_conn):
    res = client.post("/api/curation/jobs/999999/review")
    assert res.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_curation_routes.py -k review -v`
Expected: FAIL — 라우트 미등록.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/repositories/curation_repository.py` — 메서드 추가:

```python
    def job_exists(self, job_id: int) -> bool:
        """ocr_jobs에 해당 id가 존재하는지 여부."""
        with connection() as conn:
            return conn.execute(
                text("SELECT 1 FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            ).first() is not None

    def mark_reviewed(self, job_id: int) -> None:
        """잡을 검수완료로 표시하고 미처리 쌍에 reviewed_at을 찍는다."""
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET curation_reviewed = 1 WHERE id = :id"), {"id": job_id}
            )
            conn.execute(
                text(
                    "UPDATE training_pairs SET reviewed_at = CURRENT_TIMESTAMP "
                    "WHERE job_id = :id AND reviewed_at IS NULL"
                ),
                {"id": job_id},
            )
```

3-2. `app/services/curation_service.py` — 메서드 추가:

```python
    def mark_reviewed(self, job_id: int) -> dict:
        """잡을 검수완료로 표시한다. 없으면 404. 멱등."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        self.repo.mark_reviewed(job_id)
        return {"job_id": job_id, "curation_reviewed": True}
```

3-3. `app/routers/curation.py` — 라우트 추가:

```python
@router.post("/curation/jobs/{job_id}/review")
def review(job_id: int):
    """잡을 검수 완료로 표시한다(미처리 쌍 reviewed_at 스탬프)."""
    return envelope.single(_service().mark_reviewed(job_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/curation_service.py \
        apps/invoice-ocr/backend/app/routers/curation.py \
        apps/invoice-ocr/backend/tests/contract/test_curation_routes.py
git commit -m "feat(backend): POST /api/curation/jobs/{id}/review 잡 검수 완료(멱등)"
```

---

## Task 9: 이미지 엔드포인트 — `image/{kind}` + `crop/{row}` (envelope 예외, path traversal 차단)

원본/워프 전표 + 행 crop을 `FileResponse` raw 바이트로 반환. `job_id`(정수)·`row`(정수)·`kind`(enum)만 받아 서버가 `SJMJ_DATA_DIR` 하위 경로를 조립한다 — crop_ref 문자열을 raw 경로로 신뢰하지 않는다. 없는 산출물은 404.

**Files:**

- Modify: `app/repositories/curation_repository.py`
- Modify: `app/services/curation_service.py`
- Modify: `app/routers/curation.py`
- Test: `tests/contract/test_curation_routes.py` (수정)

**Interfaces:**

- Produces:
  - `CurationRepository.get_image_path(job_id: int) -> str | None` — `ocr_jobs.image_path`(원본 업로드 절대경로).
  - `CurationService.original_image(job_id) / warped_image(job_id) / crop_image(job_id, row)` → 각각 존재하는 파일 절대경로(str). 잡 없으면 `not_found`, 파일 없으면 `not_found`. 워프/crop 경로 = `SJMJ_DATA_DIR/ocr_crops/job-{job_id}/(warped.png|row-{row}.png)`.
  - 라우터 `GET /curation/jobs/{id}/image/{kind}`(kind enum=`original|warped`) → `FileResponse`. `GET /curation/jobs/{id}/crop/{row}` → `FileResponse(media_type="image/png")`.
- 의존: `kind` enum 검증 실패는 path-param `RequestValidationError`로 Task 1 핸들러가 422→400 `VALIDATION_ERROR`(details 키 `"kind"`)로 변환한다 — `test_image_invalid_kind_is_400`이 이에 의존(Task 1 선행 필수).

- [ ] **Step 1: Write the failing test**

`tests/contract/test_curation_routes.py`에 추가(상단에 `from pathlib import Path` import 추가):

```python
@pytest.fixture
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    return tmp_path


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # 최소 PNG 시그니처


def test_crop_image_returns_png(client, db_conn, _data_dir):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    crop_dir = _data_dir / "ocr_crops" / f"job-{job_id}"
    crop_dir.mkdir(parents=True)
    (crop_dir / "row-0.png").write_bytes(_PNG_BYTES)

    res = client.get(f"/api/curation/jobs/{job_id}/crop/0")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == _PNG_BYTES


def test_crop_image_404_when_file_missing(client, db_conn, _data_dir):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}/crop/0")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_warped_image_404_when_not_saved(client, db_conn, _data_dir):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}/image/warped")
    assert res.status_code == 404


def test_image_invalid_kind_is_400(client, db_conn, _data_dir):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}/image/garbage")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_original_image_returns_file(client, db_conn, _data_dir, tmp_path):
    src = tmp_path / "uploaded.png"
    src.write_bytes(_PNG_BYTES)
    with db_conn.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', :p)"),
            {"p": str(src)},
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs (crop_ref, job_id, row_index, final_label, "
                "canonical_label, status) VALUES (:r, :j, 0, 'x', 'x', 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
    res = client.get(f"/api/curation/jobs/{job_id}/image/original")
    assert res.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_curation_routes.py -k image -v`
Expected: FAIL — 라우트 미등록.

- [ ] **Step 3: Write minimal implementation**

3-1. `app/repositories/curation_repository.py` — 메서드 추가:

```python
    def get_image_path(self, job_id: int) -> str | None:
        """잡의 원본 업로드 이미지 경로를 반환한다."""
        with connection() as conn:
            return conn.execute(
                text("SELECT image_path FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            ).scalar()
```

3-2. `app/services/curation_service.py` — import + 헬퍼 + 메서드 추가:

```python
import os
from pathlib import Path
```

```python
    def _data_dir(self) -> Path:
        raw = os.environ.get("SJMJ_DATA_DIR")
        if not raw:
            # 오설정 가드: SJMJ_DATA_DIR 누락 시 명확 실패(운영 전용 — 테스트는 항상 설정).
            raise RuntimeError("SJMJ_DATA_DIR 미설정 — 이미지 경로 조립 불가")
        return Path(raw)

    def original_image(self, job_id: int) -> str:
        """원본 업로드 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self.repo.get_image_path(job_id)
        if not path or not Path(path).is_file():
            not_found("원본 이미지가 없습니다.")
        return path

    def warped_image(self, job_id: int) -> str:
        """워프된 전표 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self._data_dir() / "ocr_crops" / f"job-{job_id}" / "warped.png"
        if not path.is_file():
            not_found("워프 이미지가 없습니다.")
        return str(path)

    def crop_image(self, job_id: int, row: int) -> str:
        """행 crop 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self._data_dir() / "ocr_crops" / f"job-{job_id}" / f"row-{row}.png"
        if not path.is_file():
            not_found("crop 이미지가 없습니다.")
        return str(path)
```

3-3. `app/routers/curation.py` — import + enum + 라우트:

```python
from enum import Enum

from fastapi.responses import FileResponse
```

```python
class ImageKind(str, Enum):
    """원본/워프 전표 이미지 종류."""

    original = "original"
    warped = "warped"
```

```python
@router.get("/curation/jobs/{job_id}/image/{kind}")
def image(job_id: int, kind: ImageKind):
    """원본/워프 전표 이미지를 raw 바이트로 반환한다(envelope 예외)."""
    svc = _service()
    if kind is ImageKind.original:
        return FileResponse(svc.original_image(job_id))
    return FileResponse(svc.warped_image(job_id), media_type="image/png")


@router.get("/curation/jobs/{job_id}/crop/{row}")
def crop(job_id: int, row: int):
    """행 crop 이미지를 raw 바이트로 반환한다(envelope 예외)."""
    return FileResponse(_service().crop_image(job_id, row), media_type="image/png")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_curation_routes.py -v`
Expected: PASS (전체 큐레이션 계약 통과).

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/app/repositories/curation_repository.py \
        apps/invoice-ocr/backend/app/services/curation_service.py \
        apps/invoice-ocr/backend/app/routers/curation.py \
        apps/invoice-ocr/backend/tests/contract/test_curation_routes.py
git commit -m "feat(backend): 큐레이션 이미지 엔드포인트(image/{kind}·crop/{row}, envelope 예외)"
```

---

## Task 10: worker — 워프 전표 `warped.png` 저장

`infer_job.py`가 만들어 버리는 워프 전표 `w`를 `crop_out_dir/warped.png`로 1장 저장한다. 나머지 추론 경로 불변. warp 실패(`quad is None`) 시 저장 안 함 → 이미지 엔드포인트 404.

**Files:**

- Modify: `apps/invoice-ocr/ml/handwriting/infer_job.py` (`w` 생성 = 64행, crop 루프 = 76-81행)

**Interfaces:**

- Consumes: `crop_out_dir`(worker가 `SJMJ_DATA_DIR/ocr_crops/job-{id}`로 전달), `w`(워프+deskew 전표 ndarray).
- Produces: `crop_out_dir/warped.png`(잡당 1장). Task 9 `warped_image`가 이 경로를 읽는다.

> **검증 방식:** `infer_job`은 실모델·cv2 의존 글루라 슬라이스 단위테스트 대상이 아니다(ml/AGENTS.md
> "infer_job은 warp/embed/ocr 글루로 라이브 e2e가 검증"). 이 task의 검증은 ruff 통과 + 코드 인스펙션이며,
> 런타임 확인은 Phase C macmini 실데이터 재학습/라이브 e2e에서 이뤄진다.

- [ ] **Step 1: 변경 (테스트 불가 글루 — RED 단계 생략)**

`apps/invoice-ocr/ml/handwriting/infer_job.py`의 `w` 생성 줄(현재 64행) 바로 다음에 한 줄 추가:

```python
    w = ip.rotate(warp(bgr, quad), ip.deskew_angle(warp(bgr, quad)))
    cv2.imwrite(str(crop_out_dir / "warped.png"), w)  # 큐레이션 단계 시각화용 전표 1장
```

(`crop_out_dir`는 58행에서 이미 `Path`로 캐스팅 + `mkdir` 됨. `cv2`는 51행에서 import됨.)

- [ ] **Step 2: 린트/포맷 검증**

Run: `cd apps/invoice-ocr/ml && uv run ruff check handwriting/infer_job.py && uv run ruff format --check handwriting/infer_job.py`
Expected: PASS (no issues).

- [ ] **Step 3: 회귀 — ml 테스트 스위트(infer_job 글루 비의존, 합성만) 그대로 통과**

Run: `cd apps/invoice-ocr/ml && uv run pytest -q`
Expected: PASS (기존 합성 테스트 변동 없음 — warped.png 저장은 실모델 경로라 단위테스트에 미도달).

- [ ] **Step 4: Commit**

```bash
git add apps/invoice-ocr/ml/handwriting/infer_job.py
git commit -m "feat(ml): infer_job이 워프 전표를 warped.png로 저장(큐레이션 시각화용)"
```

---

## Task 11: api-spec.json + api-conventions.md 동기 (드리프트 차단)

`.claude/ai-context/api-spec.json`이 엔드포인트 SSoT다. 큐레이션 6 엔드포인트 + 요청/응답 스키마 + 한 줄 스캔 항목을 추가하고, 이미지 raw-file envelope 예외를 규약에 명문화한다.

**Files:**

- Modify: `.claude/ai-context/api-spec.json` (top keys: `paths` 21개, `components.schemas` 28개, `x-api-overview.endpoints`)
- Modify: `.claude/rules/api-conventions.md` (§"성공 응답 envelope 규약 → envelope 예외" 목록 = 이미 4개 항목 보유)
- Test: 없음(데이터/문서). 검증은 jq + 개수 매칭.

**Interfaces:**

- Consumes: Task 5-9가 확정한 라우트·요청/응답 형태.
- Produces: spec에 `/api/curation/*` 6 paths + `CurationJobSummary`·`CurationJobDetail`·`CurationPair`·`CurationPairPatch` 스키마 + overview 6행. 규약 문서에 이미지 예외 1항목.

- [ ] **Step 1: 현재 spec 구조 파악**

Run: `python3 -c "import json; d=json.load(open('.claude/ai-context/api-spec.json')); print('paths', len(d['paths']), 'ops', sum(len([m for m in p if m in ('get','post','put','patch','delete')]) for p in d['paths'].values()), 'overview', len(d['x-api-overview']['endpoints']))"`
Expected: paths/ops/overview 개수 출력(추가 전 기준값 기록).

- [ ] **Step 2: paths 6개 추가**

`.claude/ai-context/api-spec.json`의 `paths` 객체에 아래 키를 추가한다(기존 `/api/ocr/...` 항목들과 같은 형식·들여쓰기). 응답은 표준 success envelope를 참조하는 기존 패턴을 따르되, 이미지 2종은 binary로 표기:

```json
"/api/curation/jobs": {
  "get": {
    "summary": "검수 큐 — confirmed 잡 목록(검수상태·미처리수, 페이지네이션)",
    "tags": ["curation"],
    "parameters": [
      {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
      {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}}
    ],
    "responses": {"200": {"description": "검수 큐 목록", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"type": "array", "items": {"$ref": "#/components/schemas/CurationJobSummary"}}, "pagination": {"$ref": "#/components/schemas/Pagination"}}}}}}}
  }
},
"/api/curation/jobs/{job_id}": {
  "get": {
    "summary": "잡 상세 — 단계 이미지 신호 + 행별 쌍(top5·draft/final/canonical·supply·status)",
    "tags": ["curation"],
    "parameters": [{"name": "job_id", "in": "path", "required": true, "schema": {"type": "integer"}}],
    "responses": {
      "200": {"description": "잡 상세", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"$ref": "#/components/schemas/CurationJobDetail"}}}}}},
      "404": {"description": "잡 없음", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
    }
  }
},
"/api/curation/pairs/{id}": {
  "patch": {
    "summary": "쌍 큐레이션 — status 또는 canonical_label 갱신(Pydantic 검증)",
    "tags": ["curation"],
    "parameters": [{"name": "id", "in": "path", "required": true, "schema": {"type": "integer"}}],
    "requestBody": {"required": true, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CurationPairPatch"}}}},
    "responses": {
      "200": {"description": "갱신된 쌍", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"$ref": "#/components/schemas/CurationPair"}}}}}},
      "400": {"description": "검증 실패", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
      "404": {"description": "쌍 없음", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
    }
  }
},
"/api/curation/jobs/{job_id}/review": {
  "post": {
    "summary": "잡 검수 완료 표시(curation_reviewed=TRUE, 미처리 쌍 reviewed_at 스탬프, 멱등)",
    "tags": ["curation"],
    "parameters": [{"name": "job_id", "in": "path", "required": true, "schema": {"type": "integer"}}],
    "responses": {
      "200": {"description": "ack", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"type": "object", "properties": {"job_id": {"type": "integer"}, "curation_reviewed": {"type": "boolean"}}}}}}}},
      "404": {"description": "잡 없음", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
    }
  }
},
"/api/curation/jobs/{job_id}/image/{kind}": {
  "get": {
    "summary": "원본/워프 전표 이미지 (raw FileResponse, envelope 예외)",
    "tags": ["curation"],
    "parameters": [
      {"name": "job_id", "in": "path", "required": true, "schema": {"type": "integer"}},
      {"name": "kind", "in": "path", "required": true, "schema": {"type": "string", "enum": ["original", "warped"]}}
    ],
    "responses": {
      "200": {"description": "이미지 raw 바이트", "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
      "404": {"description": "산출물 없음", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
    }
  }
},
"/api/curation/jobs/{job_id}/crop/{row}": {
  "get": {
    "summary": "행 crop 이미지 (raw FileResponse, envelope 예외)",
    "tags": ["curation"],
    "parameters": [
      {"name": "job_id", "in": "path", "required": true, "schema": {"type": "integer"}},
      {"name": "row", "in": "path", "required": true, "schema": {"type": "integer"}}
    ],
    "responses": {
      "200": {"description": "crop raw 바이트", "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
      "404": {"description": "산출물 없음", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
    }
  }
}
```

> `#/components/schemas/Pagination`·`ErrorEnvelope`은 기존 spec에 이미 존재하는 스키마다(확인됨) — 그대로 `$ref`한다.

- [ ] **Step 3: components.schemas 4개 추가**

`components.schemas`에 추가:

```json
"CurationJobSummary": {
  "type": "object",
  "properties": {
    "job_id": {"type": "integer"},
    "invoice_id": {"type": "integer", "nullable": true},
    "curation_reviewed": {"type": "boolean"},
    "pair_count": {"type": "integer"},
    "unreviewed_count": {"type": "integer"},
    "created_at": {"type": "string", "format": "date-time"}
  }
},
"CurationPair": {
  "type": "object",
  "properties": {
    "id": {"type": "integer"},
    "crop_ref": {"type": "string"},
    "job_id": {"type": "integer"},
    "row_index": {"type": "integer"},
    "draft_label": {"type": "string", "nullable": true},
    "final_label": {"type": "string", "nullable": true},
    "canonical_label": {"type": "string", "nullable": true},
    "supply": {"type": "integer", "nullable": true},
    "status": {"type": "string", "enum": ["included", "excluded"]},
    "reviewed_at": {"type": "string", "format": "date-time", "nullable": true},
    "top5": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "sim": {"type": "number"}}}}
  }
},
"CurationJobDetail": {
  "type": "object",
  "properties": {
    "job_id": {"type": "integer"},
    "invoice_id": {"type": "integer", "nullable": true},
    "curation_reviewed": {"type": "boolean"},
    "warp_ok": {"type": "boolean"},
    "created_at": {"type": "string", "format": "date-time"},
    "pairs": {"type": "array", "items": {"$ref": "#/components/schemas/CurationPair"}}
  }
},
"CurationPairPatch": {
  "type": "object",
  "description": "status 또는 canonical_label 중 하나 이상 필수(model_validator). 검증 실패는 400 VALIDATION_ERROR.",
  "properties": {
    "status": {"type": "string", "enum": ["included", "excluded"]},
    "canonical_label": {"type": "string", "minLength": 1, "maxLength": 200}
  }
}
```

- [ ] **Step 4: x-api-overview.endpoints 6행 추가**

Step 1에서 본 기존 항목 형식(`{method, path, summary, auth, req, res, router, source}`)에 맞춰 6행 추가. 이미지 2종은 `res`에 envelope 예외 표기:

```json
{"method": "GET", "path": "/api/curation/jobs", "summary": "검수 큐 목록(페이지네이션)", "auth": "public", "req": null, "res": "CurationJobSummary[]", "router": "curation", "source": "app/routers/curation.py"},
{"method": "GET", "path": "/api/curation/jobs/{job_id}", "summary": "잡 상세(행별 top5)", "auth": "public", "req": null, "res": "CurationJobDetail", "router": "curation", "source": "app/routers/curation.py"},
{"method": "PATCH", "path": "/api/curation/pairs/{id}", "summary": "쌍 큐레이션(status/canonical_label)", "auth": "public", "req": "CurationPairPatch", "res": "CurationPair", "router": "curation", "source": "app/routers/curation.py"},
{"method": "POST", "path": "/api/curation/jobs/{job_id}/review", "summary": "잡 검수 완료(멱등)", "auth": "public", "req": null, "res": "{job_id, curation_reviewed}", "router": "curation", "source": "app/routers/curation.py"},
{"method": "GET", "path": "/api/curation/jobs/{job_id}/image/{kind}", "summary": "원본/워프 전표 이미지", "auth": "public", "req": null, "res": "binary image/png (raw FileResponse, envelope 예외)", "router": "curation", "source": "app/routers/curation.py"},
{"method": "GET", "path": "/api/curation/jobs/{job_id}/crop/{row}", "summary": "행 crop 이미지", "auth": "public", "req": null, "res": "binary image/png (raw FileResponse, envelope 예외)", "router": "curation", "source": "app/routers/curation.py"}
```

- [ ] **Step 5: api-conventions.md 이미지 예외 명문화**

`.claude/rules/api-conventions.md`의 "envelope 예외 (역사적 계약 …)" 목록에 5번 항목 추가:

```md
5. **`GET /api/curation/jobs/{job_id}/image/{kind}` · `GET /api/curation/jobs/{job_id}/crop/{row}`** —
   envelope 밖 **raw `image/png`**(`FileResponse`). `job_id`/`row`(정수)·`kind`(enum `original|warped`)만 받아
   서버가 `SJMJ_DATA_DIR` 하위 경로를 조립한다(crop_ref 문자열을 raw 경로로 신뢰하지 않음 — path traversal 차단).
   없는 산출물(백필된 구 잡의 `warped.png` 등)은 404 에러 envelope.
```

또한 라우터 구조 표(invoices/companies/...)에 `curation` 행을 추가:

```md
| curation | `app/routers/curation.py` | `/api/curation` | 큐레이션 검수 큐·잡 상세·쌍 큐레이션·검수완료·이미지 |
```

- [ ] **Step 6: JSON 유효성 + 개수 매칭 검증**

Run:

```bash
jq empty .claude/ai-context/api-spec.json && \
python3 -c "import json; d=json.load(open('.claude/ai-context/api-spec.json')); ops=sum(len([m for m in p if m in ('get','post','put','patch','delete')]) for p in d['paths'].values()); ov=len(d['x-api-overview']['endpoints']); print('ops', ops, 'overview', ov, 'match', ops==ov)"
```

Expected: `jq` 에러 없음 + `match True`(operation 수 == overview 행 수). Step 1 기준값에서 정확히 +6.

- [ ] **Step 7: Commit**

```bash
git add .claude/ai-context/api-spec.json .claude/rules/api-conventions.md
git commit -m "docs(api): 큐레이션 6 엔드포인트 + 스키마 spec 동기 + 이미지 envelope 예외 명문화"
```

---

## Phase A 최종 게이트 (CI 미러)

모든 task 완료 후, backend 디렉터리에서 CI 게이트를 그대로 재현해 통과를 확인한다.

- [ ] **Step 1: 린트/포맷**

Run: `cd apps/invoice-ocr/backend && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 2: 전체 테스트 + 커버리지 80%**

Run: `cd apps/invoice-ocr/backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80`
Expected: PASS — 신규 큐레이션 슬라이스(router/service/repository/schema) 포함 전체 통과, 커버리지 80% 이상.

- [ ] **Step 3: ml 린트/테스트(worker 변경)**

Run: `cd apps/invoice-ocr/ml && uv run ruff check . && uv run pytest -q`
Expected: PASS.

---

## Phase A Definition of Done (spec §8 대조)

- [x] `db/migration_008_*.sql`(CREATE/ALTER/백필) + `tests/fixtures/schema_test.sql` 반영 + `conftest.py:_ALL_TABLES`에 `training_pairs` 추가 — Task 2.
- [x] `.claude/ai-context/api-spec.json` paths/components/x-api-overview 갱신, 이미지 2종 raw-file envelope 예외 표기 + `.claude/rules/api-conventions.md` 예외 명문화 — Task 11.
- [x] `RequestValidationError`→400 envelope 핸들러 등록(첫 Pydantic 슬라이스 선결) — Task 1.
- [x] ruff + pytest 커버리지 80% 게이트 통과 — 최종 게이트.
- [x] confirm 머티리얼라이즈 + 큐레이션 CRUD + 이미지 path-traversal 차단 + worker warped.png — Task 3-10.

---

## 후속 Phase (별도 계획)

- **Phase B — 검수 페이지(`/curation`):** spec §6. Phase A API 위 React 페이지(`use-*` 훅 + `services/api.ts`), vitest 단위 + playwright e2e(업로드→confirm→큐레이션→검수완료). → 별도 `docs/superpowers/plans/`.
- **Phase C — 학습 브리지:** spec §7. **§7.2 선결 조건(학습 입력 SSoT (a)/(b) 결정 + 경계 하드닝)을 먼저 좌표 확정**한 뒤 §7.3 브리지 + macmini 수동 재학습 1회 실측. spec이 명시했듯 진입 전 (a)/(b)와 승격 의존 체인(`grouping`/`canon`/`fewshot`)을 확정해야 bite-sized 계획이 가능 — Phase A/B 완료 후 별도 brainstorming → 계획.

```

```
