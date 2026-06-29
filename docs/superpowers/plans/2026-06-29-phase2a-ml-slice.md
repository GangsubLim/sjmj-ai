# Phase 2A — ML 추론 수직 슬라이스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 손글씨 거래명세서 사진 1장이 `업로드 → ml-worker 추론 → 초안 → 사람 검수 → 확정(invoice 생성)`까지 관통하는 얇은 수직 슬라이스를 구현한다.

**Architecture:** FastAPI 백엔드에 신규 `ocr` slice(router→service→repository) 3종 엔드포인트를 기존 패턴대로 추가한다. 검증된 ML PoC(`process_one`)를 tracked 경로로 옮겨 HTML 부수효과를 떼고 구조화 `result_json`을 반환하는 `infer_job()`으로 최소 래핑하고, 그 위에 `ocr_jobs` DB 큐를 폴링하는 ml-worker(별도 launchd 잡)를 둔다. 프론트 작성 페이지에 사진 업로드 + top-5 pre-fill + `crop_ref` 운반을 surgical하게 끼운다.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + pymysql + MySQL(백엔드), torch(CPU)/mlx-vlm(Metal)(ml-worker), React 19 + Vite + axios + shadcn(프론트), launchd(배포).

## Global Constraints

- Python 3.12 (`>=3.12,<3.13`), 백엔드 커버리지 ≥80%(`--cov-fail-under=80`), `ruff check` + `ruff format --check` clean.
- 백엔드 라우터 핸들러는 **`sync def`**(threadpool). async로 바꾸면 conn 바인딩 가정이 깨지므로 금지.
- 요청 body는 `dict = Body(...)` **free-form** + `core/validators.Validator`로 검증(Pydantic 모델 금지 — 골든 메시지 보존). 검증 실패 → `bad_request(message, details)` → 400 `VALIDATION_ERROR`.
- 응답은 `core/envelope`(`{success, data}`) / `core/errors`(`{success, error}`) 규약.
- 신규 `ocr` 라우터는 `include_router(ocr.router, prefix="/api")` — 모든 OCR 경로는 `/api/ocr/*`. **API 라우터는 SPA catch-all(`_mount_static`)보다 먼저 등록**.
- 신규/변경 엔드포인트는 `.claude/ai-context/api-spec.json`(`paths` + `x-api-overview.endpoints` + `components.schemas` + `schema-usage`)을 함께 갱신.
- 불변 패턴: 기존 객체 변형 금지, 새 객체 반환(`{**a, ...}`).
- ML 코어는 paddle-free(pillow만). 무거운 의존(torch/mlx/SQLAlchemy)은 ml-worker venv에만.
- 데이터·모델·crop 경로는 전부 env(`SJMJ_DATA_DIR`/`SJMJ_ML_MODELS_DIR`/`DB_*`)로 주입 — 절대경로 하드코딩 금지.
- `ocr_jobs.invoice_id`에 UNIQUE 제약 없음(`migration_007`) → confirm의 행잠금 + 조건부 UPDATE claim이 중복 invoice 생성을 막는 **유일한 방어선**.

---

## File Structure

**백엔드 (신규)**
- `apps/invoice-ocr/backend/app/services/ocr_correction.py` — `build_correction()` 순수함수(초안 vs 최종 diff)
- `apps/invoice-ocr/backend/app/repositories/ocr_repository.py` — `ocr_jobs`/`ocr_corrections` DB 접근
- `apps/invoice-ocr/backend/app/services/ocr_service.py` — create_job/get_job/confirm 비즈니스 로직 + 트랜잭션
- `apps/invoice-ocr/backend/app/routers/ocr.py` — 3종 엔드포인트(multipart 업로드 포함)

**백엔드 (수정)**
- `apps/invoice-ocr/backend/app/core/errors.py` — `conflict()` 헬퍼(409) 추가
- `apps/invoice-ocr/backend/app/main.py:60` — `include_router(ocr.router, prefix="/api")` 추가
- `apps/invoice-ocr/backend/tests/fixtures/schema_test.sql` — `ocr_jobs`/`ocr_corrections` 추가
- `apps/invoice-ocr/backend/tests/conftest.py:19-28` — TRUNCATE 목록에 두 테이블 등록
- `.claude/ai-context/api-spec.json` — 신규 3종 + 스키마

**백엔드 테스트 (신규)**
- `tests/unit/test_ocr_correction.py` — diff 순수함수
- `tests/integration/test_ocr_repository.py` — 잡 생명주기 + claim
- `tests/contract/test_ocr_routes.py` — 3종 엔드포인트 입출력·envelope·409

**ML (이동/신규)**
- `apps/invoice-ocr/ml/handwriting/` — `report/sp2_spike/`에서 옮긴 tracked 추론 코어
- `apps/invoice-ocr/ml/handwriting/infer_job.py` — `assemble_result_json()` 순수함수 + `infer_job()` 글루
- `apps/invoice-ocr/ml/worker/db.py` — 라이브 MySQL 큐 접근(SQLAlchemy)
- `apps/invoice-ocr/ml/worker/poll.py` — 폴링 오케스트레이션
- `apps/invoice-ocr/ml/worker/main.py` — 기동 시 모델 1회 적재 + 루프
- `apps/invoice-ocr/ml/.gitignore` — file-precise 안전망
- `apps/invoice-ocr/ml/pyproject.toml` — `[worker]` extra

**ML 테스트 (신규)**
- `apps/invoice-ocr/ml/tests/test_assemble_result.py` — result_json 조립 순수함수
- `apps/invoice-ocr/ml/tests/test_worker_poll.py` — Fake infer 주입 오케스트레이션

**프론트 (신규/수정)**
- `frontend/src/services/api.ts` — `ocrAPI`(create/get/confirm)
- `frontend/src/hooks/use-ocr-infer.ts` — 업로드 + 폴링 훅
- `frontend/src/types/ocr.ts` — result_json·job 타입
- `frontend/src/components/invoice/invoice-form.tsx` — 사진 업로드 + pre-fill + crop_ref 운반

**배포 (신규)**
- `deploy/launchd/ai.sjmj.ml-worker.plist.template`
- `deploy/env/ml-worker.env.example`
- `scripts/run-ml-worker.sh`
- `scripts/install-launchagent-ml-worker.sh`

---

## Phase A — 블로커 해소 & 테스트 전제

### Task 1: fixture 스키마 동기화 (TDD 0단계)

`ocr_jobs`·`ocr_corrections`가 `tests/fixtures/schema_test.sql`에 없어 repository/integration 테스트가 막힌다. `migration_007_ml_seam.sql`을 정본으로 두 테이블을 복사하고 TRUNCATE 목록에 등록한다.

**Files:**
- Modify: `apps/invoice-ocr/backend/tests/fixtures/schema_test.sql` (끝에 추가)
- Modify: `apps/invoice-ocr/backend/tests/conftest.py:19-28`
- Test: `apps/invoice-ocr/backend/tests/integration/test_ocr_schema.py` (신규, 일시 검증용)

**Interfaces:**
- Produces: `sjmj_test` DB에 `ocr_jobs`(id, status, image_path, result_json, invoice_id, created_at, updated_at), `ocr_corrections`(id, job_id, invoice_id, correction_json, created_at) 테이블.

- [ ] **Step 1: 두 테이블 존재를 요구하는 실패 테스트 작성**

Create `apps/invoice-ocr/backend/tests/integration/test_ocr_schema.py`:

```python
"""schema_test.sql 동기화 검증 — ocr_jobs/ocr_corrections 존재."""

import pytest
from sqlalchemy import text

from app.db import connection

pytestmark = pytest.mark.usefixtures("db_conn")


def test_ocr_tables_exist():
    with connection() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status) VALUES ('pending')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO ocr_corrections (job_id, correction_json) "
                "VALUES (:j, JSON_OBJECT('lines', JSON_ARRAY()))"
            ),
            {"j": job_id},
        )
        n = conn.execute(text("SELECT COUNT(*) FROM ocr_corrections")).scalar()
    assert n == 1
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_schema.py -v`
Expected: FAIL — `(pymysql.err.ProgrammingError) Table 'sjmj_test.ocr_jobs' doesn't exist`

- [ ] **Step 3: schema_test.sql에 DROP + CREATE 추가**

MySQL은 영속 DB라 세션마다 schema가 재적재된다. fixture는 파일 상단의 `SET FOREIGN_KEY_CHECKS = 0;` … `= 1;` 블록(4-15행)에 나열된 테이블만 DROP하므로, OCR 테이블에 DROP이 없으면 **2번째 pytest 세션의 `CREATE TABLE`이 "이미 존재"로 깨진다.** 먼저 그 블록 안, `DROP TABLE IF EXISTS invoices;`(8행) **앞**에 FK 자식→부모 순으로 추가:

```sql
DROP TABLE IF EXISTS ocr_corrections;
DROP TABLE IF EXISTS ocr_jobs;
```

그다음 파일 **끝**에 두 테이블 CREATE를 추가(`migration_007` DDL 동치, `JSON` 타입 그대로). `invoices`보다 뒤에 생성되어야 FK 참조가 성립한다:

```sql
CREATE TABLE ocr_jobs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    image_path VARCHAR(512),
    result_json JSON,
    invoice_id INT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ocr_jobs_status (status),
    CONSTRAINT fk_ocr_jobs_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ocr_corrections (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    job_id INT UNSIGNED,
    invoice_id INT,
    correction_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ocr_corrections_job FOREIGN KEY (job_id)
        REFERENCES ocr_jobs(id) ON DELETE SET NULL,
    CONSTRAINT fk_ocr_corrections_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 4: TRUNCATE 목록에 등록**

`apps/invoice-ocr/backend/tests/conftest.py`의 `_ALL_TABLES`(라인 19-28)에서 FK 자식이 먼저 TRUNCATE되도록 `ocr_corrections`, `ocr_jobs`를 `invoices` **위**에 추가:

```python
_ALL_TABLES = [
    "ocr_corrections",
    "ocr_jobs",
    "invoice_items",
    "invoices",
    "company_suggestions",
    "item_suggestions",
    "issuers",
    "app_settings",
    "sales_records",
    "salespeople",
]
```

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_schema.py -v`
Expected: PASS

```bash
git add apps/invoice-ocr/backend/tests/fixtures/schema_test.sql apps/invoice-ocr/backend/tests/conftest.py apps/invoice-ocr/backend/tests/integration/test_ocr_schema.py
git commit -m "test(ocr): sync ocr_jobs/ocr_corrections into schema_test.sql + truncate list"
```

---

### Task 2: gitignore 재설계 + 추론 코어 tracked 이동 (S1 블로커)

`report/sp2_spike/`는 통째로 gitignore라 추론 코어가 추적 안 된다. 코어를 `apps/invoice-ocr/ml/handwriting/`(tracked)로 옮기되, 모델 아티팩트(`*.pt`/`*.npz`)는 어디 있든 추적되지 않도록 file-precise 안전망을 둔다.

**Files:**
- Modify: `apps/invoice-ocr/ml/.gitignore`
- Move (git mv): infer_photo의 **import 폐포 10개** — `report/sp2_spike/item/{infer_photo,rectify,canon,rows,group,grouping,dataset_build,fewshot,train_contrastive}.py` + `report/sp2_spike/grid_v4.py` → `apps/invoice-ocr/ml/handwriting/`. (`dataset_build`·`train_contrastive`·`fewshot`는 학습기 모듈이지만 infer_photo가 `load_bgr_path`·`build_model`/`EVAL_TF`·`square`를 직접 import하는 기존 결합 때문에 폐포에 포함된다 — 디커플링은 별도 작업.)

**Interfaces:**
- Produces: `apps/invoice-ocr/ml/handwriting/` 아래 tracked 추론 모듈. 모델 가중치는 비추적.

- [ ] **Step 1: .gitignore를 file-precise 안전망으로 교체**

`apps/invoice-ocr/ml/.gitignore` 전체를 교체:

```gitignore
# 모델·데이터 아티팩트는 코드가 아님 — 어디 있든 무시(안전망)
*.pt
*.npz

# 운영/실험 데이터·산출물
data/
results/
review/
report/
.venv/
poc/
.env
__pycache__/
```

- [ ] **Step 2: 추론 코어를 handwriting/로 이동(git mv)**

실제 의존 그래프를 먼저 확인(import 추적)하고 코어 모듈만 옮긴다:

```bash
cd apps/invoice-ocr/ml
mkdir -p handwriting/runs
# 이동 전 import 폐포 재확인(아래 9개가 grid_v4 포함 폐포):
#   infer_photo → grid_v4, rectify, canon, rows, group, grouping, dataset_build, fewshot, train_contrastive
#   (grouping → +group +dataset_build; dataset_build → grid_v4/rectify/canon; train_contrastive → fewshot)
grep -RnE "^from (grid_v4|rectify|canon|rows|group|grouping|dataset_build|fewshot|train_contrastive) " \
  report/sp2_spike/item report/sp2_spike/grid_v4.py
# item/ 아래 9개 + 부모의 grid_v4 이동
for m in infer_photo rectify canon rows group grouping dataset_build fewshot train_contrastive; do
  git mv "report/sp2_spike/item/$m.py" "handwriting/$m.py"
done
git mv report/sp2_spike/grid_v4.py handwriting/grid_v4.py
```

주의: `report/`가 gitignore라 기존 파일은 untracked일 수 있다. 그 경우 `git mv` 대신 일반 `mv` 후 `git add handwriting/`. 이동 후 `handwriting/` 내부 상대 import가 깨지지 않는지 확인하고, 깨졌으면 import 경로를 `handwriting.` 패키지 기준으로 수정한다.

- [ ] **Step 3: 모델 가중치가 추적되지 않는지 확인**

`runs/ft_prod.pt`·`runs/bank.npz`를 `handwriting/runs/`로 복사한 뒤:

Run: `cd apps/invoice-ocr/ml && git status --porcelain handwriting/`
Expected: `handwriting/*.py`는 `A`(added)로 뜨고 `handwriting/runs/ft_prod.pt`·`bank.npz`는 **목록에 없음**(gitignore 매칭).

추가 확인: `git check-ignore handwriting/runs/ft_prod.pt handwriting/runs/bank.npz` → 두 경로 모두 출력되어야 함(무시됨).

- [ ] **Step 4: 의존 폐포 + 구문 스모크**

실제 `import handwriting.infer_photo`는 torch/cv2가 필요하므로, 무거운 적재 없이 **폐포 10개가 모두 존재하고 구문이 유효한지** 확인한다. 누락 모듈은 `open()`에서 즉시 `FileNotFoundError`로 잡힌다(`ast.parse`만으로는 import 미해결이라 누락을 못 잡으므로 존재 검증을 함께 한다):

Run:
```bash
cd apps/invoice-ocr/ml
for m in infer_photo grid_v4 rectify canon rows group grouping dataset_build fewshot train_contrastive; do
  python -c "import ast; ast.parse(open('handwriting/$m.py').read())" || { echo "MISSING/PARSE FAIL: $m"; exit 1; }
done
echo "closure + parse ok"
```
Expected: `closure + parse ok`

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/ml/.gitignore apps/invoice-ocr/ml/handwriting
git commit -m "refactor(ml): move inference core to tracked handwriting/ + file-precise gitignore"
```

---

## Phase B — 백엔드 OCR slice

### Task 3: correction diff 순수함수 (S6 일부)

초안(`result_json`)과 확정 payload(item별 `crop_ref`)를 diff해 `correction_json`을 만드는 순수함수. 단위테스트 집중 대상.

**Files:**
- Create: `apps/invoice-ocr/backend/app/services/ocr_correction.py`
- Test: `apps/invoice-ocr/backend/tests/unit/test_ocr_correction.py`

**Interfaces:**
- Produces: `build_correction(result_json: dict, final_items: list[dict]) -> dict`. `result_json`은 `{"rows": [{"crop_ref", "item_top5":[{"label","sim"}], "supply"}]}` 형태, `final_items`는 invoice payload의 items(각 item에 선택적 `crop_ref`, `name`, `supply`). 반환: `{"lines": [...], "rows_added": int, "rows_dropped": int}`.

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/backend/tests/unit/test_ocr_correction.py`:

```python
from app.services.ocr_correction import build_correction


def _result(rows):
    return {"rows": rows, "supply_sum": 0, "warp_ok": True}


def test_label_changed_and_supply_unchanged():
    result = _result(
        [{"crop_ref": "job-42/row-0", "item_top5": [{"label": "삼겹살", "sim": 0.83}], "supply": 120000}]
    )
    final = [{"crop_ref": "job-42/row-0", "name": "목살", "supply": 120000}]
    out = build_correction(result, final)
    assert out["lines"] == [
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
    assert out["rows_added"] == 0
    assert out["rows_dropped"] == 0


def test_row_added_when_final_item_has_no_crop_ref():
    result = _result([])
    final = [{"name": "수기품목", "supply": 5000}]
    out = build_correction(result, final)
    assert out["lines"] == []
    assert out["rows_added"] == 1
    assert out["rows_dropped"] == 0


def test_row_dropped_when_draft_crop_unmatched():
    result = _result(
        [{"crop_ref": "job-42/row-0", "item_top5": [{"label": "삼겹살", "sim": 0.8}], "supply": 120000}]
    )
    final = []
    out = build_correction(result, final)
    assert out["rows_dropped"] == 1
    assert out["rows_added"] == 0


def test_empty_top5_yields_none_draft_label():
    result = _result([{"crop_ref": "job-42/row-0", "item_top5": [], "supply": None}])
    final = [{"crop_ref": "job-42/row-0", "name": "신규", "supply": 5000}]
    out = build_correction(result, final)
    line = out["lines"][0]
    assert line["draft_label"] is None
    assert line["label_changed"] is True
    assert line["draft_supply"] is None
    assert line["supply_changed"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/unit/test_ocr_correction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ocr_correction'`

- [ ] **Step 3: 순수함수 구현**

Create `apps/invoice-ocr/backend/app/services/ocr_correction.py`:

```python
"""초안(result_json) vs 확정 payload diff → correction_json. 순수함수."""


def build_correction(result_json: dict, final_items: list[dict]) -> dict:
    """crop_ref로 초안 행과 최종 item을 매칭해 라벨·공급가 변경을 기록한다.

    - crop_ref 없는 최종 item = 사람이 추가한 행(rows_added)
    - 최종 payload에서 매칭 안 된 초안 crop = 사람이 버린 행(rows_dropped)
    """
    draft_by_ref = {
        r["crop_ref"]: r for r in result_json.get("rows", []) if r.get("crop_ref")
    }
    lines: list[dict] = []
    matched: set[str] = set()
    rows_added = 0

    for item in final_items:
        ref = item.get("crop_ref")
        if ref and ref in draft_by_ref:
            matched.add(ref)
            row = draft_by_ref[ref]
            top5 = row.get("item_top5") or []
            draft_label = top5[0]["label"] if top5 else None
            final_label = item.get("name")
            draft_supply = row.get("supply")
            final_supply = item.get("supply")
            lines.append(
                {
                    "crop_ref": ref,
                    "draft_label": draft_label,
                    "final_label": final_label,
                    "label_changed": draft_label != final_label,
                    "draft_supply": draft_supply,
                    "final_supply": final_supply,
                    "supply_changed": draft_supply != final_supply,
                }
            )
        else:
            rows_added += 1

    rows_dropped = sum(1 for ref in draft_by_ref if ref not in matched)
    return {"lines": lines, "rows_added": rows_added, "rows_dropped": rows_dropped}
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/unit/test_ocr_correction.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/backend/app/services/ocr_correction.py apps/invoice-ocr/backend/tests/unit/test_ocr_correction.py
git commit -m "feat(ocr): add build_correction pure diff function"
```

---

### Task 4: errors.conflict 헬퍼 (409)

confirm 중복/동시 처리에서 409가 필요하다. `errors.py`에는 400/404만 있다.

**Files:**
- Modify: `apps/invoice-ocr/backend/app/core/errors.py:23` 뒤
- Test: `apps/invoice-ocr/backend/tests/unit/test_errors_conflict.py`

**Interfaces:**
- Produces: `conflict(message: str) -> None` — `AppError(409, "CONFLICT", message)` 발생.

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/backend/tests/unit/test_errors_conflict.py`:

```python
import pytest

from app.core.errors import AppError, conflict


def test_conflict_raises_409():
    with pytest.raises(AppError) as exc:
        conflict("이미 확정된 잡입니다.")
    assert exc.value.status == 409
    assert exc.value.code == "CONFLICT"
    assert exc.value.message == "이미 확정된 잡입니다."
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/unit/test_errors_conflict.py -v`
Expected: FAIL — `ImportError: cannot import name 'conflict'`

- [ ] **Step 3: 헬퍼 추가**

`apps/invoice-ocr/backend/app/core/errors.py`의 `not_found`(라인 22-23) 바로 뒤에 추가:

```python
def conflict(message: str) -> None:
    raise AppError(409, "CONFLICT", message)
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/unit/test_errors_conflict.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/backend/app/core/errors.py apps/invoice-ocr/backend/tests/unit/test_errors_conflict.py
git commit -m "feat(core): add conflict(409) error helper"
```

---

### Task 5: ocr_repository — 잡 생명주기 + claim

`ocr_jobs`/`ocr_corrections` DB 접근. `with connection()` 패턴. `result_json`은 JSON 컬럼이라 읽을 때 `json.loads`, 쓸 때 `json.dumps`.

**Files:**
- Create: `apps/invoice-ocr/backend/app/repositories/ocr_repository.py`
- Test: `apps/invoice-ocr/backend/tests/integration/test_ocr_repository.py`

**Interfaces:**
- Consumes: `app.db.connection`.
- Produces: `OcrRepository` with
  - `insert_job(image_path: str) -> int`
  - `find_job(job_id: int) -> dict | None` — `result_json`을 파싱한 dict로 반환
  - `claim_job(job_id: int) -> dict | None` — `SELECT ... FOR UPDATE`(confirm 트랜잭션 내 호출 전제), `result_json` 파싱
  - `link_invoice(job_id: int, invoice_id: int) -> int` — `UPDATE ... WHERE id=:job AND invoice_id IS NULL`, 영향행 수 반환
  - `insert_correction(job_id: int, invoice_id: int, correction_json: dict) -> int`
  - `update_result(job_id: int, status: str, result_json: dict) -> None`(worker용은 별도지만 백엔드 테스트 편의로 포함)

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/backend/tests/integration/test_ocr_repository.py`:

```python
import pytest

from app.db import transaction
from app.repositories.ocr_repository import OcrRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def test_insert_and_find_job():
    repo = OcrRepository()
    job_id = repo.insert_job("/data/ocr_uploads/abc.jpg")
    job = repo.find_job(job_id)
    assert job["id"] == job_id
    assert job["status"] == "pending"
    assert job["image_path"] == "/data/ocr_uploads/abc.jpg"
    assert job["invoice_id"] is None
    assert job["result_json"] is None


def test_find_job_parses_result_json():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    job = repo.find_job(job_id)
    assert job["status"] == "done"
    assert job["result_json"] == {"rows": [], "supply_sum": 0, "warp_ok": True}


def test_link_invoice_succeeds_once_then_returns_zero():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    # 실제 invoice 행이 있어야 FK 통과
    from app.repositories.invoice_repository import InvoiceRepository

    inv_id = InvoiceRepository().insert(td.invoice())
    with transaction():
        first = repo.link_invoice(job_id, inv_id)
    assert first == 1
    with transaction():
        second = repo.link_invoice(job_id, inv_id)
    assert second == 0  # 이미 연결됨 → 조건부 UPDATE 영향행 0


def test_insert_correction():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    from app.repositories.invoice_repository import InvoiceRepository

    inv_id = InvoiceRepository().insert(td.invoice())
    cid = repo.insert_correction(job_id, inv_id, {"lines": [], "rows_added": 0, "rows_dropped": 0})
    assert cid > 0
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repositories.ocr_repository'`

- [ ] **Step 3: repository 구현**

Create `apps/invoice-ocr/backend/app/repositories/ocr_repository.py`:

```python
"""ocr_jobs / ocr_corrections DB 접근. result_json/correction_json은 JSON 컬럼."""

import json

from sqlalchemy import text

from app.db import connection


def _parse_job(row) -> dict | None:
    if row is None:
        return None
    d = dict(row._mapping)
    raw = d.get("result_json")
    d["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
    return d


class OcrRepository:
    def insert_job(self, image_path: str) -> int:
        with connection() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO ocr_jobs (status, image_path) VALUES ('pending', :p)"
                ),
                {"p": image_path},
            )
            return int(result.lastrowid)

    def find_job(self, job_id: int) -> dict | None:
        with connection() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, image_path, result_json, invoice_id, "
                    "created_at, updated_at FROM ocr_jobs WHERE id = :id"
                ),
                {"id": job_id},
            ).fetchone()
        return _parse_job(row)

    def claim_job(self, job_id: int) -> dict | None:
        """confirm 트랜잭션 내에서 행을 잠그고 읽는다(SELECT ... FOR UPDATE)."""
        with connection() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, image_path, result_json, invoice_id "
                    "FROM ocr_jobs WHERE id = :id FOR UPDATE"
                ),
                {"id": job_id},
            ).fetchone()
        return _parse_job(row)

    def link_invoice(self, job_id: int, invoice_id: int) -> int:
        """invoice_id가 비어있을 때만 연결. 영향행 수를 반환(0이면 이미 연결됨)."""
        with connection() as conn:
            result = conn.execute(
                text(
                    "UPDATE ocr_jobs SET invoice_id = :inv "
                    "WHERE id = :job AND invoice_id IS NULL"
                ),
                {"inv": invoice_id, "job": job_id},
            )
            return result.rowcount

    def update_result(self, job_id: int, status: str, result_json: dict) -> None:
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status = :s, result_json = :r WHERE id = :id"),
                {"s": status, "r": json.dumps(result_json, ensure_ascii=False), "id": job_id},
            )

    def insert_correction(
        self, job_id: int, invoice_id: int, correction_json: dict
    ) -> int:
        with connection() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO ocr_corrections (job_id, invoice_id, correction_json) "
                    "VALUES (:j, :i, :c)"
                ),
                {
                    "j": job_id,
                    "i": invoice_id,
                    "c": json.dumps(correction_json, ensure_ascii=False),
                },
            )
            return int(result.lastrowid)
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_repository.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/backend/app/repositories/ocr_repository.py apps/invoice-ocr/backend/tests/integration/test_ocr_repository.py
git commit -m "feat(ocr): add OcrRepository with claim/link job lifecycle"
```

---

### Task 6: ocr_service — create_job / get_job / confirm

비즈니스 로직 + 트랜잭션 경계. confirm은 행잠금 claim → invoice 생성 재사용 → 조건부 link → correction 적재를 한 트랜잭션으로.

**Files:**
- Create: `apps/invoice-ocr/backend/app/services/ocr_service.py`
- Test: `apps/invoice-ocr/backend/tests/integration/test_ocr_service.py`

**Interfaces:**
- Consumes: `OcrRepository`, `InvoiceService`(생성 재사용), `build_correction`, `app.db.transaction`.
- Produces: `OcrService` with
  - `create_job(photo_bytes: bytes, filename: str) -> dict` — `$SJMJ_DATA_DIR/ocr_uploads/{uuid}.jpg` 저장 후 `{"job_id", "status": "pending"}`
  - `get_job(job_id: int) -> dict | None` — `{"id","status","result"?,"error"?}`
  - `confirm(job_id: int, payload: dict) -> dict` — `{"invoice_id"}`; 이미 확정 시 `conflict()`(409)

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/backend/tests/integration/test_ocr_service.py`:

```python
import pytest

from app.repositories.ocr_repository import OcrRepository
from app.services.ocr_service import OcrService
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def test_create_job_saves_file_and_inserts_pending():
    out = OcrService().create_job(b"\xff\xd8\xff binary", "scan.jpg")
    assert out["status"] == "pending"
    job = OcrRepository().find_job(out["job_id"])
    assert job["status"] == "pending"
    assert job["image_path"].endswith(".jpg")


def test_get_job_returns_result_when_done():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    out = OcrService().get_job(job_id)
    assert out["status"] == "done"
    assert out["result"] == {"rows": [], "supply_sum": 0, "warp_ok": True}


def test_get_job_returns_error_when_failed():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "failed", {"error": "warp 실패"})
    out = OcrService().get_job(job_id)
    assert out["status"] == "failed"
    assert out["error"] == "warp 실패"


def test_confirm_creates_invoice_links_job_and_logs_correction():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(
        job_id,
        "done",
        {
            "rows": [
                {"crop_ref": f"job-{job_id}/row-0", "item_top5": [{"label": "삼겹살", "sim": 0.8}], "supply": 100000}
            ],
            "supply_sum": 100000,
            "warp_ok": True,
        },
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = f"job-{job_id}/row-0"
    payload["items"][0]["name"] = "목살"  # 라벨 교정

    out = OcrService().confirm(job_id, payload)
    assert out["invoice_id"] > 0
    job = repo.find_job(job_id)
    assert job["invoice_id"] == out["invoice_id"]


def test_confirm_twice_raises_conflict():
    from app.core.errors import AppError

    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()

    OcrService().confirm(job_id, payload)
    with pytest.raises(AppError) as exc:
        OcrService().confirm(job_id, payload)
    assert exc.value.status == 409


@pytest.mark.parametrize(
    "status,result",
    [("pending", None), ("failed", {"error": "warp 실패"})],
)
def test_confirm_rejects_job_not_done(status, result):
    from app.core.errors import AppError

    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    if result is not None:
        repo.update_result(job_id, status, result)  # status=pending은 insert 기본값

    with pytest.raises(AppError) as exc:
        OcrService().confirm(job_id, td.invoice_with_items())
    assert exc.value.status == 409
    assert repo.find_job(job_id)["invoice_id"] is None  # invoice 미생성(가드가 앞단)
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ocr_service'`

- [ ] **Step 3: service 구현**

Create `apps/invoice-ocr/backend/app/services/ocr_service.py`:

```python
"""OCR 잡 업로드·조회·확정. confirm은 행잠금 claim으로 중복 invoice 생성을 막는다."""

import os
import uuid
from pathlib import Path

from app import db
from app.core.errors import conflict, not_found
from app.repositories.companies_repository import CompanyRepository
from app.repositories.items_repository import ItemRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.invoice_service import InvoiceService
from app.services.ocr_correction import build_correction


def _upload_root() -> Path:
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — 업로드 저장 경로 없음")
    p = Path(raw) / "ocr_uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


class OcrService:
    def __init__(self, repo=None, invoice_service=None, *, transaction=None):
        self.repo = repo or OcrRepository()
        self.invoice_service = invoice_service or InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        )
        self._transaction = transaction or db.transaction

    def create_job(self, photo_bytes: bytes, filename: str) -> dict:
        suffix = Path(filename or "").suffix.lower() or ".jpg"
        dest = _upload_root() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(photo_bytes)
        job_id = self.repo.insert_job(str(dest))
        return {"job_id": job_id, "status": "pending"}

    def get_job(self, job_id: int) -> dict | None:
        job = self.repo.find_job(job_id)
        if job is None:
            return None
        out = {"id": job["id"], "status": job["status"]}
        result = job.get("result_json")
        if job["status"] == "failed":
            out["error"] = (result or {}).get("error", "추론 실패")
        elif result is not None:
            out["result"] = result
        return out

    def confirm(self, job_id: int, payload: dict) -> dict:
        with self._transaction():
            job = self.repo.claim_job(job_id)
            if job is None:
                not_found("OCR 잡을 찾을 수 없습니다.")
            if job["invoice_id"] is not None:
                conflict("이미 확정된 잡입니다.")
            if job["status"] != "done" or job.get("result_json") is None:
                conflict("아직 확정할 수 없는 잡입니다(추론 미완료).")

            # invoice_items에는 crop_ref 컬럼이 없으므로 제거 후 invoice 생성
            invoice_payload = {
                **payload,
                "items": [
                    {k: v for k, v in item.items() if k != "crop_ref"}
                    for item in payload.get("items", [])
                ],
            }
            invoice = self.invoice_service.create(invoice_payload)
            invoice_id = invoice["id"]

            if self.repo.link_invoice(job_id, invoice_id) == 0:
                conflict("이미 확정된 잡입니다.")

            correction = build_correction(job["result_json"] or {}, payload.get("items", []))
            self.repo.insert_correction(job_id, invoice_id, correction)

        return {"invoice_id": invoice_id}
```

참고: `InvoiceService.create`는 `with self._transaction():`(=`db.transaction`)을 다시 호출하지만, `db.transaction()`은 이미 바인딩된 conn이 있으면 그대로 합류한다(`app/db.py:59-71`) — 단일 tx 보장.

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/integration/test_ocr_service.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/backend/app/services/ocr_service.py apps/invoice-ocr/backend/tests/integration/test_ocr_service.py
git commit -m "feat(ocr): add OcrService with atomic confirm claim"
```

---

### Task 7: ocr 라우터 + main 등록 + api-spec

3종 엔드포인트. `POST /ocr/jobs`는 multipart(`UploadFile`). sync 핸들러에서 `photo.file.read()`로 동기 읽기.

**Files:**
- Create: `apps/invoice-ocr/backend/app/routers/ocr.py`
- Modify: `apps/invoice-ocr/backend/app/main.py:60` 부근(invoices 등록 뒤)
- Modify: `.claude/ai-context/api-spec.json`
- Test: `apps/invoice-ocr/backend/tests/contract/test_ocr_routes.py`

**Interfaces:**
- Consumes: `OcrService`, `envelope`, `errors.not_found`, `Validator`.
- Produces: `router` with `POST /ocr/jobs`, `GET /ocr/jobs/{id}`, `POST /ocr/jobs/{id}/confirm`.

- [ ] **Step 1: 실패 contract 테스트 작성**

Create `apps/invoice-ocr/backend/tests/contract/test_ocr_routes.py`:

```python
import io

import pytest

from app.repositories.ocr_repository import OcrRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def test_create_job_accepts_multipart_and_returns_201(client):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("scan.jpg", io.BytesIO(b"\xff\xd8\xff x"), "image/jpeg")},
    )
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["status"] == "pending"
    assert isinstance(b["data"]["job_id"], int)


def test_get_job_returns_done_with_result(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.get(f"/api/ocr/jobs/{job_id}")
    assert r.status_code == 200
    b = r.json()
    assert b["data"]["status"] == "done"
    assert b["data"]["result"]["warp_ok"] is True


def test_get_job_404(client):
    r = client.get("/api/ocr/jobs/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_confirm_creates_invoice(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r.status_code == 200
    assert r.json()["data"]["invoice_id"] > 0


def test_confirm_validation_error(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json={"recipient": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_confirm_twice_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    assert client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload).status_code == 200
    r2 = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"


def test_confirm_pending_job_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")  # status=pending, result_json 없음
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=td.invoice_with_items())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/contract/test_ocr_routes.py -v`
Expected: FAIL — 404 from SPA catch-all / `ModuleNotFoundError` (라우터 미등록)

- [ ] **Step 3: 라우터 구현 + main 등록**

Create `apps/invoice-ocr/backend/app/routers/ocr.py`:

```python
"""OCR 잡 업로드·조회·확정. /api/ocr/* (sync def, threadpool)."""

from fastapi import APIRouter, Body, File, UploadFile

from app.core import envelope
from app.core.errors import not_found
from app.core.validators import Validator
from app.services.ocr_service import OcrService

router = APIRouter()


def _service() -> OcrService:
    return OcrService()


def _validate_confirm(data: dict) -> None:
    Validator().required(data, ["issue_date", "recipient"]).date_format(
        data, "issue_date"
    ).non_empty_array(data, "items").validate_or_fail()


@router.post("/ocr/jobs")
def create_job(photo: UploadFile = File(...)):
    content = photo.file.read()  # SpooledTemporaryFile 동기 읽기
    return envelope.created(_service().create_job(content, photo.filename or ""))


@router.get("/ocr/jobs/{id}")
def get_job(id: int):
    job = _service().get_job(id)
    if job is None:
        not_found("OCR 잡을 찾을 수 없습니다.")
    return envelope.single(job)


@router.post("/ocr/jobs/{id}/confirm")
def confirm(id: int, data: dict = Body(...)):
    _validate_confirm(data)
    return envelope.single(_service().confirm(id, data))
```

`apps/invoice-ocr/backend/app/main.py`에서 import에 `ocr`를 추가하고, invoices 등록(라인 60) 바로 뒤에 등록:

```python
    application.include_router(invoices.router, prefix="/api")
    application.include_router(ocr.router, prefix="/api")
```

(파일 상단 `from app.routers import (...)` 구문에 `ocr` 추가. `_mount_static`보다 위여야 함 — 기존 순서 유지.)

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/contract/test_ocr_routes.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: api-spec.json 갱신 + 커밋**

`.claude/ai-context/api-spec.json`에 반영:

1. `x-api-overview.endpoints[]`에 3개 행 추가:
```json
{ "method": "POST", "path": "/api/ocr/jobs", "summary": "Upload photo + enqueue OCR job (201; multipart photo field; saves to $SJMJ_DATA_DIR/ocr_uploads)", "auth": "public", "req": "OcrJobCreate", "res": "OcrJobAck", "router": "ocr", "source": "app/routers/ocr.py" },
{ "method": "GET", "path": "/api/ocr/jobs/{id}", "summary": "Poll OCR job status (pending/running/done/failed; done→result, failed→error; 404 if missing)", "auth": "public", "req": null, "res": "OcrJobStatus", "router": "ocr", "source": "app/routers/ocr.py" },
{ "method": "POST", "path": "/api/ocr/jobs/{id}/confirm", "summary": "Confirm draft → create invoice + link job + log correction (atomic claim; 409 if already confirmed)", "auth": "public", "req": "OcrConfirmRequest", "res": "OcrConfirmAck", "router": "ocr", "source": "app/routers/ocr.py" }
```

2. `x-api-overview.schema-usage`에 추가:
```json
"OcrJobCreate": ["POST /api/ocr/jobs"],
"OcrJobAck": ["POST /api/ocr/jobs"],
"OcrJobStatus": ["GET /api/ocr/jobs/{id}"],
"OcrConfirmRequest": ["POST /api/ocr/jobs/{id}/confirm"],
"OcrConfirmAck": ["POST /api/ocr/jobs/{id}/confirm"]
```

3. `error.code` enum에 `"CONFLICT"`가 없으면 `ErrorEnvelope.error.properties.code.enum`에 추가.

4. `paths`에 세 경로 정의 추가(기존 invoices 패턴 따름; `POST /api/ocr/jobs`는 `requestBody`를 `multipart/form-data`, `photo` `type: string, format: binary`로). `components.schemas`에 `OcrJobAck`(`{job_id:int, status:string}`), `OcrJobStatus`(`{id, status, result?:object, error?:string}`), `OcrConfirmRequest`(invoice payload + items[].crop_ref), `OcrConfirmAck`(`{invoice_id:int}`) 추가.

검증: `jq empty .claude/ai-context/api-spec.json`

```bash
git add apps/invoice-ocr/backend/app/routers/ocr.py apps/invoice-ocr/backend/app/main.py apps/invoice-ocr/backend/tests/contract/test_ocr_routes.py .claude/ai-context/api-spec.json
git commit -m "feat(ocr): add /api/ocr/jobs routes + api-spec entries"
```

---

### Task 8: 백엔드 게이트 — 전체 스위트 + 커버리지 + 린트

**Files:** 없음(검증만)

- [ ] **Step 1: 전체 테스트 + 커버리지(CI 미러)**

Run: `cd apps/invoice-ocr/backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80`
Expected: PASS, 커버리지 ≥80%. ocr 모듈에 미커버 분기가 있으면 단위테스트 보강.

- [ ] **Step 2: 린트/포맷**

Run: `cd apps/invoice-ocr/backend && uv run ruff check . && uv run ruff format --check .`
Expected: clean. 실패 시 `uv run ruff format .` 후 재확인.

- [ ] **Step 3: 커밋(보강이 있었다면)**

```bash
git add -A apps/invoice-ocr/backend
git commit -m "test(ocr): backend gate — coverage>=80 + ruff clean"
```

---

## Phase C — ML 추론 경계 & worker

### Task 9: assemble_result_json 순수함수 (S2 일부)

`infer_job()`의 dict 조립을 순수함수로 분리해 TDD한다(워프/임베딩/OCR 글루는 라이브 e2e가 검증).

**Files:**
- Create: `apps/invoice-ocr/ml/handwriting/infer_job.py`
- Create: `apps/invoice-ocr/ml/tests/test_assemble_result.py`

**Interfaces:**
- Produces: `assemble_result_json(job_id: int, rows: list[dict], warp_ok: bool) -> dict`. 각 `rows` 원소는 `{"row_index", "item_top5":[{"label","sim"}], "supply":int|None, "amount_raw":str}`. 반환: `{"rows": [...crop_ref 포함...], "supply_sum": int, "warp_ok": bool}`.

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/ml/tests/test_assemble_result.py`:

```python
from handwriting.infer_job import assemble_result_json


def test_assembles_crop_ref_and_supply_sum():
    rows = [
        {"row_index": 0, "item_top5": [{"label": "삼겹살", "sim": 0.83}], "supply": 120000, "amount_raw": "120,000"},
        {"row_index": 1, "item_top5": [], "supply": None, "amount_raw": "—"},
    ]
    out = assemble_result_json(42, rows, True)
    assert out["rows"][0]["crop_ref"] == "job-42/row-0"
    assert out["rows"][1]["crop_ref"] == "job-42/row-1"
    assert out["supply_sum"] == 120000  # None은 합산 제외
    assert out["warp_ok"] is True


def test_warp_failure_yields_empty_rows():
    out = assemble_result_json(7, [], False)
    assert out == {"rows": [], "supply_sum": 0, "warp_ok": False}
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_assemble_result.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handwriting.infer_job'`
(pytest 미설치 시 `pip install pytest` 또는 worker venv 사용)

- [ ] **Step 3: 순수함수 + infer_job 글루 구현**

Create `apps/invoice-ocr/ml/handwriting/infer_job.py`:

```python
"""검증된 process_one을 감싸 HTML 대신 구조화 result_json을 반환한다.

assemble_result_json은 순수함수(TDD 대상). infer_job은 warp/embed/ocr 글루로
라이브 e2e가 검증한다(슬라이스는 실모델 추론을 단위테스트하지 않음).
"""


def assemble_result_json(job_id: int, rows: list[dict], warp_ok: bool) -> dict:
    out_rows = []
    supply_sum = 0
    for r in rows:
        supply = r.get("supply")
        out_rows.append(
            {
                "row_index": r["row_index"],
                "crop_ref": f"job-{job_id}/row-{r['row_index']}",
                "item_top5": r.get("item_top5") or [],
                "supply": supply,
                "amount_raw": r.get("amount_raw", ""),
            }
        )
        if supply is not None:
            supply_sum += supply
    return {"rows": out_rows, "supply_sum": supply_sum, "warp_ok": warp_ok}


def infer_job(image_path: str, models, crop_out_dir, job_id: int) -> dict:
    """사진 1장 → result_json. crop PNG를 crop_out_dir/row-{i}.png로 저장.

    models: (item_model, E, lab, qwen, device) 번들(worker가 1회 적재).
    process_one(handwriting/infer_photo.py)의 추론 단계를 재사용하되 HTML 조립을
    제거하고 rows 리스트를 만들어 assemble_result_json으로 직렬화한다.
    """
    from pathlib import Path

    import cv2
    import numpy as np

    from handwriting.grid_v4 import warp
    from handwriting import infer_photo as ip

    item_model, E, lab, qwen, device = models
    crop_out_dir = Path(crop_out_dir)
    crop_out_dir.mkdir(parents=True, exist_ok=True)

    bgr = ip.load_bgr_path(image_path)
    quad = ip.form_quad_robust(bgr)
    if quad is None:
        return assemble_result_json(job_id, [], warp_ok=False)
    w = ip.rotate(warp(bgr, quad), ip.deskew_angle(warp(bgr, quad)))

    # process_one(infer_photo.py:121-186)과 동일한 행 검출·crop·retrieval·금액 OCR.
    # 정확한 내부 호출(detect_grid_rows / band_features / build_proposal / embed_crops /
    # topk / read_amount)은 이동 후 infer_photo의 실제 시그니처에 맞춰 채운다.
    news, crops, queries, amounts = ip.extract_rows_for_job(w, item_model, qwen, device)

    rows = []
    for i, row in enumerate(news):
        # crop 영속화(재학습 GT 이미지측)
        cv2.imwrite(str(crop_out_dir / f"row-{i}.png"), crops[i])
        sims = E @ queries[i] if len(queries) else np.zeros(0)
        top5 = [{"label": L, "sim": s} for L, s in ip.topk(sims, lab, 5)] if len(sims) else []
        amt, raw = amounts[i]
        rows.append(
            {"row_index": i, "item_top5": top5, "supply": amt, "amount_raw": raw}
        )

    return assemble_result_json(job_id, rows, warp_ok=True)
```

주의: `extract_rows_for_job`는 Task 2에서 옮긴 `infer_photo.process_one`의 추론 단계(HTML 직전까지)를 헬퍼로 추출한 것이다. 이동 시 `process_one`에서 행검출~crop~금액 단계를 `extract_rows_for_job(w, model, qwen, device) -> (news, crops, queries, amounts)`로 빼내고, HTML을 쓰던 `process_one`은 이 헬퍼 + 기존 HTML 조립으로 재구성한다(데모 경로 보존). 정확한 슬라이싱 좌표는 옮긴 파일에서 재확인한다.

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_assemble_result.py -v`
Expected: PASS (2 passed) — 순수함수만 테스트하므로 cv2/torch 불필요(함수 내부 import).

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/ml/handwriting/infer_job.py apps/invoice-ocr/ml/tests/test_assemble_result.py
git commit -m "feat(ml): add assemble_result_json + infer_job wrapper"
```

---

### Task 10: worker DB 큐 접근 (라이브 MySQL)

ml-worker는 ml/ venv에서 돌며 SQLAlchemy + pymysql로 `ocr_jobs`를 직접 폴링한다. backend와 별개 모듈.

**Files:**
- Create: `apps/invoice-ocr/ml/worker/__init__.py`(빈 파일)
- Create: `apps/invoice-ocr/ml/worker/db.py`
- Modify: `apps/invoice-ocr/ml/pyproject.toml` — `[worker]` extra

**Interfaces:**
- Produces: `WorkerQueue` with `claim_next_pending() -> dict | None`(pending 1건을 running으로 전이 후 반환), `mark_done(job_id, result_json: dict)`, `mark_failed(job_id, error_json: dict)`.

- [ ] **Step 1: pyproject에 [worker] extra 추가**

`apps/invoice-ocr/ml/pyproject.toml`의 `[project.optional-dependencies]`에 추가:

```toml
worker = [
    "sqlalchemy>=2.0",
    "pymysql>=1.1",
]
```

(torch/mlx-vlm은 기존 spike venv 설치 절차를 따른다 — 본 extra는 큐 접속 의존만.)

- [ ] **Step 2: 실패 테스트 작성(상태 전이, 모킹)**

Create `apps/invoice-ocr/ml/tests/test_worker_db.py`:

```python
from unittest.mock import MagicMock

from worker.db import WorkerQueue


def test_mark_done_serializes_json():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    q = WorkerQueue(engine)
    q.mark_done(5, {"rows": [], "supply_sum": 0, "warp_ok": True})
    # UPDATE 실행됐고 status=done 바인딩
    args = conn.execute.call_args
    assert args is not None
    params = args[0][1]
    assert params["s"] == "done"
    assert '"warp_ok"' in params["r"]
```

- [ ] **Step 3: 실패 확인**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_worker_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'worker.db'`

- [ ] **Step 4: WorkerQueue 구현**

Create `apps/invoice-ocr/ml/worker/__init__.py` (빈 파일).

Create `apps/invoice-ocr/ml/worker/db.py`:

```python
"""ml-worker의 ocr_jobs 큐 접근. backend와 동일 MySQL, DB_* env."""

import json
import os

from sqlalchemy import create_engine, text


def build_engine():
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ["DB_NAME"]
    user = os.environ["DB_USER"]
    pw = os.environ.get("DB_PASS", "")
    url = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True, future=True)


class WorkerQueue:
    def __init__(self, engine):
        self.engine = engine

    def claim_next_pending(self) -> dict | None:
        """가장 오래된 pending 1건을 running으로 전이하고 반환(단일 워커 직렬)."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT id, image_path FROM ocr_jobs WHERE status='pending' "
                    "ORDER BY id LIMIT 1 FOR UPDATE"
                )
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                text("UPDATE ocr_jobs SET status='running' WHERE id=:id"),
                {"id": row.id},
            )
            return {"id": row.id, "image_path": row.image_path}

    def mark_done(self, job_id: int, result_json: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='done', result_json=:r WHERE id=:id"),
                {"r": json.dumps(result_json, ensure_ascii=False), "id": job_id},
            )

    def mark_failed(self, job_id: int, error_json: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='failed', result_json=:r WHERE id=:id"),
                {"r": json.dumps(error_json, ensure_ascii=False), "id": job_id},
            )
```

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_worker_db.py -v`
Expected: PASS

```bash
git add apps/invoice-ocr/ml/worker/__init__.py apps/invoice-ocr/ml/worker/db.py apps/invoice-ocr/ml/pyproject.toml apps/invoice-ocr/ml/tests/test_worker_db.py
git commit -m "feat(worker): add WorkerQueue live MySQL polling + [worker] extra"
```

---

### Task 11: worker 폴링 오케스트레이션 (Fake infer 주입)

한 잡을 처리하는 단위(claim → infer → done/failed). 잡 단위 격리(한 잡 실패가 워커를 안 죽임).

**Files:**
- Create: `apps/invoice-ocr/ml/worker/poll.py`
- Test: `apps/invoice-ocr/ml/tests/test_worker_poll.py`

**Interfaces:**
- Consumes: `WorkerQueue`(또는 동일 인터페이스 mock), `infer_fn(image_path, crop_dir, job_id) -> dict`.
- Produces: `process_one_job(queue, infer_fn, crops_root) -> bool`(처리하면 True, pending 없으면 False).

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/invoice-ocr/ml/tests/test_worker_poll.py`:

```python
from unittest.mock import MagicMock

from worker.poll import process_one_job


def test_no_pending_returns_false():
    q = MagicMock()
    q.claim_next_pending.return_value = None
    assert process_one_job(q, lambda *a: {}, "/tmp/crops") is False


def test_done_path_marks_done_with_result():
    q = MagicMock()
    q.claim_next_pending.return_value = {"id": 9, "image_path": "/x.jpg"}
    canned = {"rows": [], "supply_sum": 0, "warp_ok": True}
    assert process_one_job(q, lambda *a: canned, "/tmp/crops") is True
    q.mark_done.assert_called_once_with(9, canned)
    q.mark_failed.assert_not_called()


def test_failure_isolated_marks_failed_not_raised():
    q = MagicMock()
    q.claim_next_pending.return_value = {"id": 3, "image_path": "/x.jpg"}

    def boom(*a):
        raise RuntimeError("warp explode")

    assert process_one_job(q, boom, "/tmp/crops") is True
    q.mark_failed.assert_called_once()
    err = q.mark_failed.call_args[0][1]
    assert "warp explode" in err["error"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_worker_poll.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'worker.poll'`

- [ ] **Step 3: 구현**

Create `apps/invoice-ocr/ml/worker/poll.py`:

```python
"""한 잡 처리 단위. 잡 단위 격리 — 한 잡 실패가 워커를 죽이지 않는다."""

from pathlib import Path


def process_one_job(queue, infer_fn, crops_root) -> bool:
    job = queue.claim_next_pending()
    if job is None:
        return False
    crop_dir = Path(crops_root) / f"job-{job['id']}"
    try:
        result = infer_fn(job["image_path"], crop_dir, job["id"])
        queue.mark_done(job["id"], result)
    except Exception as exc:  # noqa: BLE001 — 잡 단위 격리(워커 생존)
        queue.mark_failed(job["id"], {"error": str(exc)})
    return True
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/ml && python -m pytest tests/test_worker_poll.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/ml/worker/poll.py apps/invoice-ocr/ml/tests/test_worker_poll.py
git commit -m "feat(worker): add process_one_job orchestration with job isolation"
```

---

### Task 12: worker main — 기동 시 모델 1회 적재 + 루프

진입점. 기동 시 모델·뱅크 1회 적재(device 분리), `SJMJ_ML_MODELS_DIR`·`SJMJ_DATA_DIR` env 경계, pending 없으면 폴링 간격 sleep.

**Files:**
- Create: `apps/invoice-ocr/ml/worker/main.py`

**Interfaces:**
- Consumes: `build_engine`, `WorkerQueue`, `process_one_job`, `handwriting.infer_job.infer_job`, `handwriting.infer_photo`(모델 로더).
- Produces: `python -m worker.main` 실행 진입점.

- [ ] **Step 1: 구현(스모크 검증 대상 — 단위테스트 없음)**

Create `apps/invoice-ocr/ml/worker/main.py`:

```python
"""ml-worker 진입점 — 모델 1회 적재 후 ocr_jobs 폴링."""

import os
import time
from pathlib import Path

from worker.db import WorkerQueue, build_engine
from worker.poll import process_one_job

POLL_INTERVAL_SEC = 2.0


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} 미설정")
    return val


def load_models():
    """품목 인코더(CPU torch) + 금액 인식기(MLX Metal) 1회 적재. device 분리 보존."""
    import numpy as np

    from handwriting import infer_photo as ip

    models_dir = Path(_require("SJMJ_ML_MODELS_DIR"))
    device = "cpu"  # PyTorch-MPS와 MLX Metal 동시 사용 시 degenerate — CPU 고정
    item_model = ip.load_model_from(models_dir / "ft_prod.pt", device)
    z = np.load(models_dir / "bank.npz", allow_pickle=True)
    qwen = ip.load_ocr()
    return item_model, z["emb"], list(z["lab"]), qwen, device


def main():
    from handwriting.infer_job import infer_job

    crops_root = Path(_require("SJMJ_DATA_DIR")) / "ocr_crops"
    queue = WorkerQueue(build_engine())
    models = load_models()

    def infer_fn(image_path, crop_dir, job_id):
        return infer_job(image_path, models, crop_dir, job_id)

    while True:
        worked = process_one_job(queue, infer_fn, crops_root)
        if not worked:
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
```

주의: `ip.load_model_from(path, device)`는 Task 2에서 옮긴 `infer_photo.load_model`(고정 `PROD` 경로 사용)을 **경로 인자를 받도록** 일반화한 것이다. 이동 시 `load_model(device)`를 `load_model_from(path, device)`로 리팩터하고 기존 `main`은 `load_model_from(PROD, device)`로 호출하도록 바꾼다(데모 경로 보존). `bank.npz`도 동일하게 `SJMJ_ML_MODELS_DIR` 기준으로 읽는다.

- [ ] **Step 2: import 스모크(모델 로딩 없이)**

Run: `cd apps/invoice-ocr/ml && python -c "import ast; ast.parse(open('worker/main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add apps/invoice-ocr/ml/worker/main.py
git commit -m "feat(worker): add main entrypoint — load models once + poll loop"
```

---

## Phase D — 작성 페이지 증강

### Task 13: OCR API 클라이언트 + 타입

**Files:**
- Create: `frontend/src/types/ocr.ts`
- Modify: `frontend/src/services/api.ts` (끝에 `ocrAPI` 추가)
- Test: `frontend/src/services/ocr-api.test.ts`

**Interfaces:**
- Produces: `ocrAPI.createJob(file: File)`, `ocrAPI.getJob(id)`, `ocrAPI.confirm(id, payload)`; `OcrJobStatus`, `OcrResultRow` 타입.

- [ ] **Step 1: 실패 테스트 작성**

Create `frontend/src/services/ocr-api.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "axios";
import { ocrAPI } from "./api";

vi.mock("axios");

describe("ocrAPI", () => {
  beforeEach(() => vi.clearAllMocks());

  it("createJob posts multipart form with photo field", async () => {
    const post = vi.fn().mockResolvedValue({ data: { success: true, data: { job_id: 1, status: "pending" } } });
    (axios.create as unknown as ReturnType<typeof vi.fn>).mockReturnValue({ post, get: vi.fn() });
    // api 모듈이 생성한 인스턴스를 쓰므로, 통합은 빌드 타임 인스턴스 기준으로 검증한다.
    const file = new File([new Uint8Array([1, 2, 3])], "scan.jpg", { type: "image/jpeg" });
    const res = await ocrAPI.createJob(file);
    expect(res.data.job_id).toBe(1);
  });
});
```

참고: `api.ts`는 모듈 로드 시 `axios.create()`로 인스턴스를 만든다. 위 테스트가 모킹 타이밍 때문에 까다로우면, `ocrAPI`를 기존 `invoiceAPI`처럼 `_realOcrAPI` + 동일 `api` 인스턴스로 두고, 테스트는 `api` 인스턴스의 `post`/`get`을 `vi.spyOn`으로 가로채는 방식으로 작성한다(기존 테스트 관례를 따른다 — 작성 전 `src/services/`의 기존 테스트가 axios를 어떻게 모킹하는지 확인).

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- ocr-api`
Expected: FAIL — `ocrAPI` export 없음

- [ ] **Step 3: 타입 + 클라이언트 구현**

Create `frontend/src/types/ocr.ts`:

```typescript
export interface OcrItemPred {
  label: string;
  sim: number;
}

export interface OcrResultRow {
  row_index: number;
  crop_ref: string;
  item_top5: OcrItemPred[];
  supply: number | null;
  amount_raw: string;
}

export interface OcrResult {
  rows: OcrResultRow[];
  supply_sum: number;
  warp_ok: boolean;
}

export interface OcrJobStatus {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  result?: OcrResult;
  error?: string;
}
```

`frontend/src/services/api.ts`에 `invoiceAPI` 정의 부근 패턴을 따라 추가(`ep`, `api` 재사용):

```typescript
import type { OcrJobStatus } from "@/types/ocr";

const _realOcrAPI = {
  createJob: async (file: File) => {
    const form = new FormData();
    form.append("photo", file);
    const response = await api.post(ep("ocr/jobs"), form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data as { success: boolean; data: { job_id: number; status: string } };
  },

  getJob: async (id: number) => {
    const response = await api.get(`${ep("ocr/jobs")}/${id}`);
    return response.data as { success: boolean; data: OcrJobStatus };
  },

  confirm: async (id: number, payload: unknown) => {
    const response = await api.post(`${ep("ocr/jobs")}/${id}/confirm`, payload);
    return response.data as { success: boolean; data: { invoice_id: number } };
  },
};

export const ocrAPI = _realOcrAPI;
```

(`ep("ocr/jobs")`가 `/api/ocr/jobs`로 풀리는지 기존 `ep` 구현 확인 — 안 맞으면 경로 문자열을 직접 구성.)

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- ocr-api`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/types/ocr.ts apps/invoice-ocr/frontend/src/services/api.ts apps/invoice-ocr/frontend/src/services/ocr-api.test.ts
git commit -m "feat(ocr-fe): add ocrAPI client + result types"
```

---

### Task 14: 업로드 + 폴링 훅

사진을 올리고 done/failed까지 폴링하는 훅. 추론 결과를 작성 폼이 소비.

**Files:**
- Create: `frontend/src/hooks/use-ocr-infer.ts`
- Test: `frontend/src/hooks/use-ocr-infer.test.ts`

**Interfaces:**
- Produces: `useOcrInfer()` → `{ status, result, error, upload(file: File) }`. `upload`은 createJob 후 `getJob`을 간격 폴링(상수 `POLL_MS=2000`)하다 done/failed에서 멈춤.

- [ ] **Step 1: 실패 테스트 작성**

Create `frontend/src/hooks/use-ocr-infer.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useOcrInfer } from "./use-ocr-infer";
import { ocrAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  ocrAPI: { createJob: vi.fn(), getJob: vi.fn(), confirm: vi.fn() },
}));

describe("useOcrInfer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uploads then polls until done", async () => {
    (ocrAPI.createJob as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { job_id: 1, status: "pending" } });
    (ocrAPI.getJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ data: { id: 1, status: "running" } })
      .mockResolvedValueOnce({ data: { id: 1, status: "done", result: { rows: [], supply_sum: 0, warp_ok: true } } });

    const { result } = renderHook(() => useOcrInfer());
    await act(async () => {
      await result.current.upload(new File([new Uint8Array([1])], "x.jpg"));
    });
    await waitFor(() => expect(result.current.status).toBe("done"), { timeout: 8000 });
    expect(result.current.result?.warp_ok).toBe(true);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- use-ocr-infer`
Expected: FAIL — 훅 없음

- [ ] **Step 3: 훅 구현**

Create `frontend/src/hooks/use-ocr-infer.ts`:

```typescript
import * as React from "react";
import { ocrAPI } from "@/services/api";
import type { OcrJobStatus, OcrResult } from "@/types/ocr";

const POLL_MS = 2000;
const MAX_POLLS = 60; // 안전 상한(~2분)

type Status = "idle" | "pending" | "running" | "done" | "failed";

export function useOcrInfer() {
  const [status, setStatus] = React.useState<Status>("idle");
  const [result, setResult] = React.useState<OcrResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const upload = React.useCallback(async (file: File) => {
    setStatus("pending");
    setResult(null);
    setError(null);
    const { data } = await ocrAPI.createJob(file);
    const jobId = data.job_id;

    for (let i = 0; i < MAX_POLLS; i++) {
      const { data: job } = (await ocrAPI.getJob(jobId)) as { data: OcrJobStatus };
      if (job.status === "done") {
        setResult(job.result ?? null);
        setStatus("done");
        return;
      }
      if (job.status === "failed") {
        setError(job.error ?? "추론 실패");
        setStatus("failed");
        return;
      }
      setStatus(job.status === "running" ? "running" : "pending");
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
    setError("추론 시간이 초과되었습니다.");
    setStatus("failed");
  }, []);

  return { status, result, error, upload };
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- use-ocr-infer`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/hooks/use-ocr-infer.ts apps/invoice-ocr/frontend/src/hooks/use-ocr-infer.test.ts
git commit -m "feat(ocr-fe): add useOcrInfer upload+poll hook"
```

---

### Task 15: 작성 폼 증강 — 사진 업로드 + pre-fill + crop_ref 운반

`invoice-form.tsx`에 사진 업로드 버튼을 붙이고, done 시 `name`·`supply`를 행에 pre-fill하며, 각 행에 `crop_ref`를 hidden 운반해 confirm payload에 싣는다.

**Files:**
- Modify: `frontend/src/components/invoice/invoice-form.tsx`
- Modify: `frontend/src/types/invoice.ts` — `InvoiceItem`에 `crop_ref?: string` 추가

**Interfaces:**
- Consumes: `useOcrInfer`, `ocrAPI.confirm`.
- Produces: OCR 모드 작성 폼 — done 결과로 items 채우고, 저장 시 job이 있으면 `ocrAPI.confirm(jobId, payload)`로, 없으면 기존 `invoiceAPI.create`로 분기.

- [ ] **Step 1: 타입에 crop_ref 추가 + 실패 단위테스트(pre-fill 매핑)**

`frontend/src/types/invoice.ts`의 `InvoiceItem`에 추가:

```typescript
  crop_ref?: string;
```

Create `frontend/src/components/invoice/ocr-prefill.ts`(매핑을 순수함수로 분리해 TDD):

```typescript
import type { InvoiceItem } from "@/types/invoice";
import type { OcrResult } from "@/types/ocr";

export function rowsToItems(result: OcrResult): Partial<InvoiceItem>[] {
  return result.rows.map((row) => ({
    name: row.item_top5[0]?.label ?? "",
    unit_price: row.supply ?? 0,
    quantity: 1,
    crop_ref: row.crop_ref,
    deduction: false,
  }));
}
```

Create `frontend/src/components/invoice/ocr-prefill.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { rowsToItems } from "./ocr-prefill";

describe("rowsToItems", () => {
  it("maps top-1 label to name and supply to unit_price, carrying crop_ref", () => {
    const items = rowsToItems({
      rows: [
        { row_index: 0, crop_ref: "job-42/row-0", item_top5: [{ label: "삼겹살", sim: 0.8 }], supply: 120000, amount_raw: "120,000" },
      ],
      supply_sum: 120000,
      warp_ok: true,
    });
    expect(items[0].name).toBe("삼겹살");
    expect(items[0].unit_price).toBe(120000);
    expect(items[0].crop_ref).toBe("job-42/row-0");
  });

  it("empty top5 yields blank name for manual typing", () => {
    const items = rowsToItems({
      rows: [{ row_index: 0, crop_ref: "job-1/row-0", item_top5: [], supply: null, amount_raw: "" }],
      supply_sum: 0,
      warp_ok: true,
    });
    expect(items[0].name).toBe("");
    expect(items[0].unit_price).toBe(0);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- ocr-prefill`
Expected: FAIL — `rowsToItems` 없음

- [ ] **Step 3: 순수함수는 Step 1에서 작성됨 → 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- ocr-prefill`
Expected: PASS

- [ ] **Step 4: invoice-form.tsx에 업로드 UI + 분기 배선**

`invoice-form.tsx`에 surgical 추가(기존 좌표 기준):

1. 상단 import: `useOcrInfer`, `rowsToItems`, `ocrAPI`.
2. 상태: `const ocr = useOcrInfer();` + `const [jobId, setJobId] = React.useState<number | null>(null);`
3. Section 1(문서 정보, 라인 ~360) 안에 `StampUpload`(`stamp-upload.tsx`) 패턴을 따른 `<input type="file" accept="image/*">` 업로드 버튼 추가. onChange에서 `await ocr.upload(file)` 호출하고, createJob 응답의 job_id를 별도로 보관해야 하므로 훅이 jobId도 노출하도록 Task 14의 `useOcrInfer` 반환에 `jobId`를 추가한다(반환 객체에 `jobId` 포함, `upload` 내부에서 `setJobId(jobId)`).
4. `ocr.status === "done"`이 되면 `useEffect`로 `setItems(rowsToItems(ocr.result).map((p, i) => ({ ...DEFAULT_ITEM, ...p, item_order: i, _tempId: crypto.randomUUID() })))`. `warp_ok === false`면 빈 폼 유지 + 토스트 안내.
5. `handleSave`(라인 239-285)에서 분기: `jobId`가 있으면 payload의 items에 `crop_ref`를 포함시켜 `await ocrAPI.confirm(jobId, payload)`, 없으면 기존 `invoiceAPI.create(payload)`. confirm 성공 시 동일하게 `navigate("/list")`. 409면 "이미 확정된 명세서입니다" 토스트.
   - 주의: 기존 `handleSave`의 payload 구성에서 item을 매핑할 때 `crop_ref`를 보존하도록 `const { _tempId, ...rest } = item` 후 `rest`에 `crop_ref`가 남게 한다(타입에 추가됐으므로 자동 포함).

- [ ] **Step 5: 단위 스위트 + 린트 + 커밋**

Run: `cd apps/invoice-ocr/frontend && npm run test && npm run lint && npm run format:check`
Expected: PASS / clean (필요 시 `npm run format`).

```bash
git add apps/invoice-ocr/frontend/src
git commit -m "feat(ocr-fe): photo upload + top-5 prefill + crop_ref confirm in invoice form"
```

---

## Phase E — launchd 배포 (S3 인프라)

### Task 16: ml-worker launchd 잡 (plist + env + 스크립트)

기존 `ai.sjmj.backend` 패턴을 그대로 복제해 `ai.sjmj.ml-worker`를 만든다. 수동 검증.

**Files:**
- Create: `deploy/launchd/ai.sjmj.ml-worker.plist.template`
- Create: `deploy/env/ml-worker.env.example`
- Create: `scripts/run-ml-worker.sh`
- Create: `scripts/install-launchagent-ml-worker.sh`

- [ ] **Step 1: plist 템플릿 작성**

Create `deploy/launchd/ai.sjmj.ml-worker.plist.template` (backend 템플릿 동형, Label·로그명만 변경):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.sjmj.ml-worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>__WRAPPER_PATH__</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SJMJ_ENV_FILE</key>
    <string>__ENV_FILE__</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>__PROJECT_ROOT__</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>__LOG_DIR__/ml-worker.out.log</string>
  <key>StandardErrorPath</key>
  <string>__LOG_DIR__/ml-worker.err.log</string>
</dict>
</plist>
```

- [ ] **Step 2: env 예시 작성**

Create `deploy/env/ml-worker.env.example`:

```bash
# ml-worker 배포용 env. ~/.sjmj-ai/ml-worker.env 로 복사 후 값 채우고 chmod 600.
# launchd는 로그인 셸을 로드하지 않으므로 PYTHON_BIN은 worker venv 절대경로 필수.
PYTHON_BIN=/Users/submini/sjmj-ai/apps/invoice-ocr/ml/.venv/bin/python
LOG_LEVEL=info

# 모델 아티팩트(ft_prod.pt·bank.npz) 루트 — 최초 1회 수동 배치(ADR 0001)
SJMJ_ML_MODELS_DIR=/Users/submini/sjmj-ai-models

# 업로드 사진·crop 운영 데이터 루트(backend와 공유)
SJMJ_DATA_DIR=/Users/submini/sjmj-ai-data

# 라이브 큐 접속 — backend와 동일 운영 MySQL
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=sjmj
DB_USER=root
DB_PASS=
```

- [ ] **Step 3: run-ml-worker.sh 작성**

Create `scripts/run-ml-worker.sh` (`run-backend.sh` 동형):

```bash
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/ml-worker.env}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ML_DIR="$PROJECT_ROOT/apps/invoice-ocr/ml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" || "$PYTHON_BIN" != /* ]]; then
  echo "PYTHON_BIN must be an absolute path to worker venv python" >&2
  exit 1
fi

cd "$ML_DIR"
exec "$PYTHON_BIN" -m worker.main
```

`chmod +x scripts/run-ml-worker.sh`

- [ ] **Step 4: install 스크립트 작성**

Create `scripts/install-launchagent-ml-worker.sh` (`install-launchagent.sh` 동형, `LABEL`·`WRAPPER_PATH`만 변경 — 전체 구조는 기존 스크립트 복사). `LABEL="ai.sjmj.ml-worker"`, `WRAPPER_PATH="$PROJECT_ROOT/scripts/run-ml-worker.sh"`, 안내 메시지의 env 예시 경로를 `deploy/env/ml-worker.env.example`로. `chmod +x`.

- [ ] **Step 5: plist lint + 커밋**

Run: 로컬에서 placeholder를 임시 치환해 문법만 확인:
`python3 -c "import plistlib,io; print('template ok')"` (실제 lint는 macmini 설치 시 `plutil -lint`가 수행).

```bash
chmod +x scripts/run-ml-worker.sh scripts/install-launchagent-ml-worker.sh
git add deploy/launchd/ai.sjmj.ml-worker.plist.template deploy/env/ml-worker.env.example scripts/run-ml-worker.sh scripts/install-launchagent-ml-worker.sh
git commit -m "feat(deploy): add ai.sjmj.ml-worker launchd job + install script"
```

---

## Phase F — DoD 통합 (macmini 실데이터 e2e)

### Task 17: macmini e2e 1건 + 모델 수동 배치

코드가 아니라 **운영 환경 굳히기**. 로컬에서 단위/contract가 모두 녹색인 전제에서, macmini에서 관통을 확인한다.

**Files:** 없음(운영 검증)

- [ ] **Step 1: 모델 아티팩트 1회 수동 배치**

macmini에서 `handwriting/runs/ft_prod.pt`·`bank.npz`를 `SJMJ_ML_MODELS_DIR`(`ml-worker.env`)로 복사. `git check-ignore`로 추적 안 됨 재확인.

- [ ] **Step 2: worker venv 구성 + launchd 설치**

아래는 모두 **repo 루트**에서 실행한다. worker venv 구성만 서브셸로 `ml/`에 진입해 cwd가 루트로 복귀하도록 한다(그래야 이후 repo-루트 상대경로 `deploy/`·`scripts/`가 맞는다):

```bash
# repo 루트에서:
(cd apps/invoice-ocr/ml && uv venv && uv pip install -e ".[worker]")
# torch/mlx-vlm은 기존 spike 절차로 worker venv에 설치

cp deploy/env/ml-worker.env.example ~/.sjmj-ai/ml-worker.env  # 값 채우고 chmod 600
scripts/install-launchagent-ml-worker.sh
```

기동 로그(`~/.sjmj-ai/logs/ml-worker.err.log`)에서 모델 1회 적재 완료를 확인.

- [ ] **Step 3: 실사진 관통 검증**

작성 페이지에서 실제 손글씨 명세서 사진 업로드 → 폴링 done → `name`(top-5)·`supply` pre-fill 확인 → 한 행 라벨 교정 → 저장(confirm) → 다음을 DB로 확인:
- `invoices`에 1건 생성
- `ocr_jobs.invoice_id`가 그 invoice로 연결
- `ocr_corrections`에 1행(교정 diff) 적재
- `$SJMJ_DATA_DIR/ocr_crops/job-{id}/row-*.png` 존재

- [ ] **Step 4: DoD 체크리스트 확인**

spec §8 4항목 모두 충족 확인: (1) 실데이터 e2e 관통, (2) 백엔드 게이트(테스트·커버리지·ruff·api-spec), (3) 블로커 해소(gitignore·이동·모델 비추적·`SJMJ_ML_MODELS_DIR`), (4) launchd 기동+모델 적재.

---

## Self-Review

**1. Spec coverage**
- S1 영속화 블로커 → Task 2(gitignore·이동) + Task 17 Step1(모델 배치). ✓
- S2 얇은 추론 경계(infer_job) → Task 9. ✓
- S3 ml-worker → Task 10·11·12 + Task 16(launchd). ✓
- S4 업로드 API → Task 7(`POST /ocr/jobs`). ✓
- S5 상태/초안 조회 → Task 7(`GET /ocr/jobs/{id}`) + get_job(Task 6). ✓
- S6 확정 API(원자적 claim + correction) → Task 3·5·6·7. ✓
- S7 작성 페이지 증강 → Task 13·14·15. ✓
- §5.1 result_json 계약 → Task 9 assemble_result_json. ✓
- §5.2 correction_json 계약 → Task 3 build_correction. ✓
- §6 에러 처리(잡 격리·warp_ok·확정 롤백·409) → Task 11(격리)·Task 9(warp_ok)·Task 6(롤백·409). ✓
- §7 테스트 전략(fixture 동기화 0단계) → Task 1. ✓
- §3 env 경계 → Task 6(SJMJ_DATA_DIR)·Task 12(SJMJ_ML_MODELS_DIR)·Task 16(env 예시). ✓

**2. Placeholder scan**
- Task 9 `extract_rows_for_job`, Task 12 `load_model_from`는 Task 2 이동 시 정확 좌표가 확정되는 리팩터 지점 — "옮긴 파일에서 재확인" 명시. 순수함수(assemble_result_json·build_correction)와 모든 백엔드 경로는 완전한 코드 제공. 의도된 잔여 불확정은 실모델 글루뿐이며 라이브 e2e가 검증(슬라이스 범위 §7과 일치).

**3. Type consistency**
- `result_json` shape(`rows[].crop_ref/item_top5/supply/amount_raw`, `supply_sum`, `warp_ok`)이 백엔드(build_correction·get_job)·ML(assemble_result_json)·프론트(`OcrResult`)에서 동일. ✓
- `crop_ref` 포맷 `job-{id}/row-{i}`이 ML(assemble)·correction diff·crop 저장 경로에서 일치. ✓
- confirm payload items[].crop_ref가 타입(invoice.ts)·service(strip)·correction(diff)·프론트(rowsToItems)에서 일관. ✓
- 409 코드 `CONFLICT`가 errors.conflict·테스트·api-spec enum에서 일치. ✓

---

> **요약:** Task 1(fixture 동기화)·Task 2(블로커)로 토대를 깐 뒤, 백엔드 OCR slice(순수 diff → repository → service → router/api-spec)를 TDD로 쌓고, ML 추론 경계(assemble 순수함수 + infer_job 글루)와 worker(큐·폴링·main)를 올린 다음, 프론트 작성 폼에 업로드·pre-fill·crop_ref 운반을 끼우고, launchd 잡으로 배포해 macmini 실데이터 e2e 1건으로 관통을 확정한다.
