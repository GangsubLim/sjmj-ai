# Phase 1B — invoices vertical slice + 백엔드 foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SJMJ-Web 정본 Front Controller의 invoices 7라우트(+export CSV, +duplicate)를 FastAPI로 계약 동치 포팅하고, 6리소스가 공유할 백엔드 foundation(config·db·envelope·errors·validators·실DB 골든 하니스)을 세운다.

**Architecture:** PHP 4-layer(`Router→Controller→Service→Repository(PDO)`)를 FastAPI `router→service→repository(SQLAlchemy Core text())`로 동형 이식. 구조화 envelope `{success,data,pagination}`(결정 B). 골든 228의 invoices 몫(Controller 19 + Service 13 + Repository 17 + Export 2 = 51)을 pytest로 계약 동치 이식. 이 슬라이스가 통과하면 나머지 5리소스를 workflow로 팬아웃한다.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 Core(sync) + PyMySQL, pydantic-settings, pytest + httpx TestClient, 실 MySQL `sjmj_test`(tx 롤백 격리).

**근거 문서:** spec [`2026-06-28-phase1b-backend-api-porting-spec.md`](../specs/2026-06-28-phase1b-backend-api-porting-spec.md), 인벤토리 [`2026-06-28-phase1b-contract-inventory.md`](../specs/2026-06-28-phase1b-contract-inventory.md). 원본 소스: `~/projects/SJMJ-Web/backend`.

## Global Constraints

- envelope 정본 = 구조화: `list 200 {success:true,data:[...],pagination:{page,limit,total,totalPages}}` · `single 200 {success:true,data}` · `created 201 {success:true,data}` · `deleted 200 {success:true,data:null,message}` · `error {success:false,error:{code,message,details?}}`(code∈VALIDATION_ERROR/NOT_FOUND/SERVER_ERROR). 미처리 예외→500 SERVER_ERROR.
- export는 envelope 밖 `StreamingResponse(media_type="text/csv")` — JSON 래핑 금지.
- 검증 메시지·에러코드는 PHP와 **문자 그대로** 일치(골든 동치): 필수=`"{field} 필드는 필수입니다."`, maxLength=`"{field}은(는) {max}자 이하여야 합니다."`, dateFormat=`"{field}은(는) YYYY-MM-DD 형식이어야 합니다."`, nonEmptyArray=`"{field}은(는) 1개 이상의 항목이 필요합니다."`, validateOrFail 메시지=`"입력값이 올바르지 않습니다."`.
- 숫자/불리언 직렬화: DB INT→int, BOOLEAN(TINYINT)→int `1|0`(프론트는 truthy 처리). bool로 변환 금지(골든 동치).
- DB 접근은 raw SQL(`sqlalchemy.text()`) + 바인드 파라미터. 문자열 보간으로 SQL 조립 금지(정렬 컬럼은 화이트리스트 매핑).
- 모든 신규 .py는 sync. async 금지(KISS).
- 작업 디렉터리: `apps/invoice-ocr/backend/`. 모든 경로는 이 기준 상대.
- 커버리지 80%↑. TDD(RED→GREEN→REFACTOR), task마다 독립 커밋(conventional commits).

---

## File Structure

```
apps/invoice-ocr/backend/
  pyproject.toml                      # MODIFY: deps 추가
  app/
    config.py                         # MODIFY: pydantic-settings Settings + DB_*·SJMJ_* env
    db.py                             # CREATE: SQLAlchemy engine + connection/transaction 컨텍스트 + 테스트 주입
    main.py                           # MODIFY: 예외 핸들러 등록 + invoices 라우터 include
    core/
      __init__.py                     # CREATE
      errors.py                       # CREATE: AppError + 핸들러
      envelope.py                     # CREATE: list/single/created/deleted 응답
      validators.py                   # CREATE: Validator(fluent) PHP 동치
    models/
      __init__.py                     # CREATE
      invoice.py                      # CREATE: pydantic 요청 모델(선택) — 검증은 Validator로
    repositories/
      __init__.py                     # CREATE
      invoice_repository.py           # CREATE: PDO InvoiceRepository 동형(text() SQL)
    services/
      __init__.py                     # CREATE
      invoice_service.py              # CREATE: InvoiceService 동형(tx·usage·duplicate)
      export_service.py               # CREATE: ExportService 동형(sanitize+CSV)
    routers/
      __init__.py                     # CREATE
      invoices.py                     # CREATE: 7라우트
  tests/
    conftest.py                       # MODIFY/CREATE: 실DB fixture(tx 롤백) + 엔진 주입
    fixtures/
      schema_test.sql                 # CREATE: 8테이블 통합 DDL(SJMJ-Web fixture 포팅)
      test_data.py                    # CREATE: TestData 팩토리 포팅
    unit/
      test_config.py                  # CREATE: Settings env override 골든(AppConfig 동치)
      test_validators.py              # CREATE: Validator 골든(15 동치)
      test_envelope.py                # CREATE: envelope 형태 골든
      test_invoice_service.py         # CREATE: Service 골든(13 동치, mock repo)
      test_export_service.py          # CREATE: sanitizeCsvField 골든(2 동치)
    integration/
      test_invoice_repository.py      # CREATE: Repository 골든(17 동치, 실DB)
    contract/
      test_invoices_routes.py         # CREATE: Controller 골든(19 동치, TestClient)
```

---

## Task 0: 의존성 + 실DB 테스트 하니스

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/fixtures/schema_test.sql`, `tests/fixtures/test_data.py`, `tests/conftest.py`(또는 기존 수정)

**Interfaces:**
- Produces: pytest fixture `db_conn`(실DB Connection, 테스트마다 tx 롤백), `client`(FastAPI TestClient), `test_data` 팩토리 함수들(`invoice()`, `invoice_item()`, `invoice_with_items()`, `company()`, `item()`).

- [ ] **Step 1: deps 추가** — `pyproject.toml`의 `dependencies`에 추가, dev에 추가.

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "sqlalchemy>=2.0.0",
    "pymysql>=1.1.0",
    "pydantic-settings>=2.4.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "httpx>=0.27.0",
    "pytest-cov>=5.0.0",
]
```

Run: `cd apps/invoice-ocr/backend && uv sync` → Expected: 의존성 설치 성공.

- [ ] **Step 2: 통합 DDL 포팅** — `tests/fixtures/schema_test.sql` 생성. SJMJ-Web의 통합 스키마를 그대로 포팅(8테이블: issuers·invoices·invoice_items·company_suggestions·item_suggestions·app_settings·salespeople·sales_records + app_settings 4시드).

```bash
cp ~/projects/SJMJ-Web/backend/tests/Fixtures/schema-test.sql \
   apps/invoice-ocr/backend/tests/fixtures/schema_test.sql
```
검증: 파일에 `CREATE TABLE invoices`·`CREATE TABLE invoice_items`(FK CASCADE)·`company_suggestions`(UNIQUE company_name)·`item_suggestions`·`app_settings` 시드(`default_vat_rate`='0.1' 등)가 포함되는지 육안 확인. 1A `db/schema.sql`+migration과 동일 컬럼이어야 한다(2026-06-28 실측 일치).

- [ ] **Step 3: TestData 팩토리 포팅** — `tests/fixtures/test_data.py` 생성(PHP `Fixtures/TestData.php` 동치). invoices 슬라이스에 필요한 팩토리만 우선(나머지는 팬아웃 시 추가).

```python
"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php 포팅."""
from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def invoice(overrides: dict | None = None) -> dict:
    return _merge({
        "document_title": "거 래 명 세 서",
        "issue_date": "2026-05-15",
        "recipient": "한양운수",
        "recipient2": "",
        "vehicle_no": "12가3456",
        "memo": None,
        "show_stamp": 1,
        "issuer_id": None,
        "total_supply": 100000,
        "total_vat": 10000,
        "grand_total": 110000,
    }, overrides)


def invoice_item(overrides: dict | None = None) -> dict:
    return _merge({
        "name": "엔진오일",
        "quantity": 2,
        "unit": "EA",
        "unit_price": 50000,
        "supply": 100000,
        "vat": 10000,
        "total": 110000,
        "deduction": 0,
    }, overrides)


def invoice_with_items(overrides: dict | None = None) -> dict:
    base = invoice()
    base["items"] = [
        invoice_item(),
        invoice_item({"name": "브레이크오일"}),
        invoice_item({"name": "에어필터"}),
    ]
    return _merge(base, overrides)


def company(overrides: dict | None = None) -> dict:
    return _merge({
        "company_name": "한양운수", "recipient2": None, "phone": "02-1234-5678",
        "fax": None, "sms_number_type": "phone", "address": "서울시 강남구",
        "business_number": "1234567890", "notes": None,
    }, overrides)


def item(overrides: dict | None = None) -> dict:
    return _merge({
        "item_name": "엔진오일", "default_unit": "EA", "default_unit_price": 50000,
        "category": "oil", "notes": None,
    }, overrides)
```

> 주의: `invoice_with_items()`의 recipient='한양운수'·items 3개·item.name이 골든(InvoiceServiceTest의 `incrementUsageByName('한양운수')`·`exactly(3)`)과 일치해야 한다.

- [ ] **Step 4: conftest fixture** — `tests/conftest.py` 생성. 실DB 엔진 주입 + tx 롤백 격리(DatabaseTestCase 동치) + TestClient.

```python
import os
import pytest
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient

from app import db as dbmod


def _test_url() -> str:
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ.get("DB_NAME", "sjmj_test")
    user = os.environ.get("DB_USER", "sjmj_test")
    pw = os.environ.get("DB_PASS", "sjmj_test_pass")
    return f"mysql+pymysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"


@pytest.fixture(scope="session")
def _engine():
    engine = create_engine(_test_url(), pool_pre_ping=True)
    schema = open(os.path.join(os.path.dirname(__file__), "fixtures", "schema_test.sql")).read()
    with engine.begin() as conn:
        for stmt in [s for s in schema.split(";") if s.strip()]:
            conn.execute(text(stmt))
    yield engine
    engine.dispose()


@pytest.fixture
def db_conn(_engine):
    """테스트마다 트랜잭션을 열고 끝나면 롤백 — 격리. repo/service가 바인딩된 conn 재사용."""
    dbmod.set_test_engine(_engine)
    conn = _engine.connect()
    trans = conn.begin()
    token = dbmod._conn_var.set(conn)
    try:
        yield conn
    finally:
        dbmod._conn_var.reset(token)
        trans.rollback()
        conn.close()
        dbmod.reset_engine()


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)
```

- [ ] **Step 5: Commit**

```bash
git add apps/invoice-ocr/backend/pyproject.toml apps/invoice-ocr/backend/tests/
git commit -m "test(backend): Phase1B 실DB 골든 하니스 + TestData 팩토리 + deps"
```

> conftest는 `app.db`·`app.main`을 import하므로 Task 1~3 완료 전엔 collection 에러가 난다. Task 0은 파일만 두고, 실제 fixture 동작 검증은 Task 5(첫 실DB 테스트)에서 한다.

---

## Task 1: config.py — pydantic-settings (AppConfig 골든 동치)

**Files:**
- Modify: `app/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `class Settings(BaseSettings)` (필드 `db_host,db_port,db_name,db_user,db_pass,sjmj_port,sjmj_static_dir,sjmj_data_dir,sjmj_db_backup`), `get_settings() -> Settings`(cache). 기존 `get_port()`·`get_static_dir()`는 Settings 위임으로 유지.

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_config.py`.

```python
import importlib
from app import config


def _fresh(monkeypatch, env: dict):
    for k in ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config)
    config.get_settings.cache_clear()
    return config.get_settings()


def test_db_env_override(monkeypatch):
    s = _fresh(monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": "p"})
    assert (s.db_host, s.db_name, s.db_user, s.db_pass) == ("db1", "n", "u", "p")


def test_empty_password_respected(monkeypatch):
    # 빈 비밀번호는 유효한 값 — 미설정과 구분(AppConfig 골든)
    s = _fresh(monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": ""})
    assert s.db_pass == ""
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_config.py -v` → Expected: FAIL(`Settings`/`get_settings` 없음).

- [ ] **Step 3: 구현** — `app/config.py`를 다음으로 교체(기존 `get_port`/`get_static_dir` 시그니처 보존).

```python
"""백엔드 설정 — pydantic-settings 경계 검증(시스템 경계 입력)."""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # 런타임 MySQL 연결 (PHP config/app.php DB_* 동치, 빈 PASS 존중)
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "kslim"
    db_user: str = "kslim"
    db_pass: str = ""

    # 서버
    sjmj_port: int = 8400
    sjmj_static_dir: str | None = None

    # ML 이음새(Phase 2 소비, 1B는 자리만)
    sjmj_data_dir: str | None = None
    sjmj_db_backup: str | None = None

    class Config:
        env_prefix = ""


@lru_cache
def get_settings() -> Settings:
    return Settings(
        db_host=_env("DB_HOST", "localhost"),
        db_port=int(_env("DB_PORT", "3306")),
        db_name=_env("DB_NAME", "kslim"),
        db_user=_env("DB_USER", "kslim"),
        db_pass=_env("DB_PASS", ""),
        sjmj_port=int(_env("SJMJ_PORT", "8400")),
        sjmj_static_dir=_env("SJMJ_STATIC_DIR", None),
        sjmj_data_dir=_env("SJMJ_DATA_DIR", None),
        sjmj_db_backup=_env("SJMJ_DB_BACKUP", None),
    )


import os


def _env(key: str, default):
    v = os.environ.get(key)
    return v if v is not None else default


def get_port() -> int:
    try:
        return get_settings().sjmj_port
    except ValueError:
        return 8400


def get_static_dir() -> Path | None:
    raw = get_settings().sjmj_static_dir
    candidate = Path(raw) if raw else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None
```

> `db_pass` 빈 문자열 존중: `os.environ.get("DB_PASS")`가 `""`면 `""` 사용(미설정 None과 구분) — `_env`는 `is not None` 분기로 처리. AppConfig 골든 동치.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_config.py -v` → Expected: PASS(2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config.py
git commit -m "feat(backend): pydantic-settings Settings — DB_*/SJMJ_* env 경계(AppConfig 골든 동치)"
```

---

## Task 2: db.py — SQLAlchemy 엔진 + connection/transaction 컨텍스트

**Files:**
- Create: `app/db.py`
- Test: `tests/unit/test_db.py`

**Interfaces:**
- Produces: `get_engine() -> Engine`, `set_test_engine(engine)`, `reset_engine()`, `_conn_var: ContextVar`, `connection()`(contextmanager, 바인딩 conn 재사용 or 새 conn), `transaction()`(contextmanager, tx 시작+conn 바인딩). repository는 `with connection() as conn: conn.execute(text(...), params)`. service는 `with self.transaction(): ...`.

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_db.py`(엔진 없이 컨텍스트 의미만; sqlite in-memory로 검증).

```python
from sqlalchemy import create_engine, text
from app import db as dbmod


def test_connection_reuses_bound_conn():
    engine = create_engine("sqlite://")
    dbmod.set_test_engine(engine)
    try:
        conn = engine.connect()
        token = dbmod._conn_var.set(conn)
        try:
            with dbmod.connection() as c1, dbmod.connection() as c2:
                assert c1 is conn and c2 is conn  # 바인딩 conn 재사용
        finally:
            dbmod._conn_var.reset(token)
            conn.close()
    finally:
        dbmod.reset_engine()


def test_transaction_binds_connection():
    engine = create_engine("sqlite://")
    dbmod.set_test_engine(engine)
    try:
        with dbmod.transaction() as conn:
            assert dbmod._conn_var.get() is conn
            conn.execute(text("SELECT 1"))
        assert dbmod._conn_var.get() is None  # 종료 후 해제
    finally:
        dbmod.reset_engine()
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_db.py -v` → Expected: FAIL(`app.db` 없음).

- [ ] **Step 3: 구현** — `app/db.py`.

```python
"""DB 연결 — SQLAlchemy Engine + connection/transaction 컨텍스트(PHP Database 싱글톤 동형)."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Engine, create_engine

from .config import get_settings

_engine: Engine | None = None
_conn_var: ContextVar = ContextVar("sjmj_conn", default=None)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        s = get_settings()
        url = (
            f"mysql+pymysql://{s.db_user}:{s.db_pass}@{s.db_host}:{s.db_port}"
            f"/{s.db_name}?charset=utf8mb4"
        )
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def set_test_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def reset_engine() -> None:
    global _engine
    _engine = None


@contextmanager
def connection():
    """현재 바인딩된 conn이 있으면 재사용, 없으면 엔진에서 새로 연다."""
    existing = _conn_var.get()
    if existing is not None:
        yield existing
        return
    with get_engine().connect() as conn:
        yield conn


@contextmanager
def transaction():
    """트랜잭션 시작 + conn 바인딩(내부 repo가 같은 conn 재사용). 이미 tx면 중첩 재사용."""
    existing = _conn_var.get()
    if existing is not None:
        yield existing
        return
    with get_engine().begin() as conn:
        token = _conn_var.set(conn)
        try:
            yield conn
        finally:
            _conn_var.reset(token)
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_db.py -v` → Expected: PASS(2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/unit/test_db.py
git commit -m "feat(backend): SQLAlchemy 엔진 + connection/transaction 컨텍스트(테스트 주입 가능)"
```

---

## Task 3: core/errors.py + envelope.py — 구조화 응답

**Files:**
- Create: `app/core/__init__.py`, `app/core/errors.py`, `app/core/envelope.py`
- Test: `tests/unit/test_envelope.py`

**Interfaces:**
- Produces: `class AppError(Exception)`(`status,code,message,details`), `not_found(msg)`·`bad_request(msg,details=None)`(raise), `register_error_handlers(app)`. envelope: `list_response(data,pagination)`·`single(data)`·`created(data)`·`deleted(message)` → `JSONResponse`.

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_envelope.py`.

```python
import json
from app.core import envelope


def _body(resp):
    return json.loads(bytes(resp.body))


def test_list_response_shape():
    r = envelope.list_response([{"id": 1}], {"page": 1, "limit": 20, "total": 1, "totalPages": 1})
    assert r.status_code == 200
    b = _body(r)
    assert b["success"] is True
    assert b["data"] == [{"id": 1}]
    assert b["pagination"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}


def test_created_is_201():
    r = envelope.created({"id": 5})
    assert r.status_code == 201
    assert _body(r) == {"success": True, "data": {"id": 5}}


def test_deleted_shape():
    r = envelope.deleted("거래명세서가 삭제되었습니다.")
    assert _body(r) == {"success": True, "data": None, "message": "거래명세서가 삭제되었습니다."}
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_envelope.py -v` → Expected: FAIL(import 에러).

- [ ] **Step 3: 구현** — `app/core/__init__.py`(빈 파일), `app/core/errors.py`, `app/core/envelope.py`.

`app/core/errors.py`:
```python
"""구조화 에러 — PHP Response::error / HttpResponseException 동형."""
from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def bad_request(message: str, details: dict | None = None):
    raise AppError(400, "VALIDATION_ERROR", message, details)


def not_found(message: str = "Resource not found"):
    raise AppError(404, "NOT_FOUND", message)


def _error_body(code: str, message: str, details: dict | None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"success": False, "error": err}


async def _app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status, content=_error_body(exc.code, exc.message, exc.details))


async def _unhandled_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=_error_body("SERVER_ERROR", str(exc), None))


def register_error_handlers(app) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
```

`app/core/envelope.py`:
```python
"""구조화 성공 응답 — PHP Response:: 동형."""
from fastapi.responses import JSONResponse


def list_response(data: list, pagination: dict) -> JSONResponse:
    return JSONResponse(status_code=200, content={"success": True, "data": data, "pagination": pagination})


def single(data) -> JSONResponse:
    return JSONResponse(status_code=200, content={"success": True, "data": data})


def created(data) -> JSONResponse:
    return JSONResponse(status_code=201, content={"success": True, "data": data})


def deleted(message: str) -> JSONResponse:
    return JSONResponse(status_code=200, content={"success": True, "data": None, "message": message})
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_envelope.py -v` → Expected: PASS(3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/ tests/unit/test_envelope.py
git commit -m "feat(backend): 구조화 envelope + AppError 핸들러(Response:: 동형)"
```

---

## Task 4: core/validators.py — Validator (15 골든 동치)

**Files:**
- Create: `app/core/validators.py`
- Test: `tests/unit/test_validators.py`

**Interfaces:**
- Produces: `class Validator`(fluent): `.required(data,fields)`·`.max_length(data,field,max)`·`.date_format(data,field)`·`.business_number(data,field)`·`.numeric(data,field)`·`.non_empty_array(data,field)`·`.fails()`·`.errors`·`.validate_or_fail()`(실패 시 `bad_request("입력값이 올바르지 않습니다.", errors)`). 메시지 PHP 문자 동치.

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_validators.py`(대표 케이스; 전수는 `~/projects/SJMJ-Web/backend/tests/Unit/Core/ValidatorTest.php` 15개 동치 포팅).

```python
import pytest
from app.core.validators import Validator
from app.core.errors import AppError


def test_required_missing_and_blank():
    v = Validator().required({"a": "", "b": "  ", "c": "ok"}, ["a", "b", "c", "d"])
    assert v.fails()
    assert v.errors["a"] == "a 필드는 필수입니다."
    assert "b" in v.errors and "d" in v.errors and "c" not in v.errors


def test_max_length():
    v = Validator().max_length({"recipient": "가" * 101}, "recipient", 100)
    assert v.errors["recipient"] == "recipient은(는) 100자 이하여야 합니다."


def test_date_format():
    assert Validator().date_format({"issue_date": "2026-05-15"}, "issue_date").fails() is False
    v = Validator().date_format({"issue_date": "2026/05/15"}, "issue_date")
    assert v.errors["issue_date"] == "issue_date은(는) YYYY-MM-DD 형식이어야 합니다."


def test_business_number():
    assert Validator().business_number({"b": "123-45-67890"}, "b").fails() is False  # 10자리
    assert Validator().business_number({"b": "123456789"}, "b").fails() is True       # 9자리


def test_non_empty_array():
    assert Validator().non_empty_array({"items": [1]}, "items").fails() is False
    v = Validator().non_empty_array({"items": []}, "items")
    assert v.errors["items"] == "items은(는) 1개 이상의 항목이 필요합니다."


def test_validate_or_fail_raises():
    with pytest.raises(AppError) as ei:
        Validator().required({}, ["x"]).validate_or_fail()
    assert ei.value.status == 400 and ei.value.code == "VALIDATION_ERROR"
    assert ei.value.details == {"x": "x 필드는 필수입니다."}
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_validators.py -v` → Expected: FAIL(`Validator` 없음).

- [ ] **Step 3: 구현** — `app/core/validators.py`.

```python
"""Validator — PHP core/Validator.php 동형(메시지 문자 동치)."""
from datetime import datetime
import re

from .errors import bad_request


class Validator:
    def __init__(self):
        self.errors: dict[str, str] = {}

    def required(self, data: dict, fields: list[str]) -> "Validator":
        for f in fields:
            v = data.get(f)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                self.errors[f] = f"{f} 필드는 필수입니다."
        return self

    def max_length(self, data: dict, field: str, max_: int) -> "Validator":
        v = data.get(field)
        if v is not None and len(str(v)) > max_:
            self.errors[field] = f"{field}은(는) {max_}자 이하여야 합니다."
        return self

    def date_format(self, data: dict, field: str) -> "Validator":
        v = data.get(field)
        if v is not None:
            try:
                d = datetime.strptime(str(v), "%Y-%m-%d")
                ok = d.strftime("%Y-%m-%d") == str(v)
            except ValueError:
                ok = False
            if not ok:
                self.errors[field] = f"{field}은(는) YYYY-MM-DD 형식이어야 합니다."
        return self

    def business_number(self, data: dict, field: str) -> "Validator":
        v = data.get(field)
        if v is not None and v != "":
            digits = re.sub(r"[^0-9]", "", str(v))
            if len(digits) != 10:
                self.errors[field] = "사업자번호는 10자리 숫자여야 합니다."
        return self

    def numeric(self, data: dict, field: str) -> "Validator":
        v = data.get(field)
        if v is not None:
            try:
                float(v)
            except (TypeError, ValueError):
                self.errors[field] = f"{field}은(는) 숫자여야 합니다."
        return self

    def non_empty_array(self, data: dict, field: str) -> "Validator":
        v = data.get(field)
        if not isinstance(v, list) or len(v) == 0:
            self.errors[field] = f"{field}은(는) 1개 이상의 항목이 필요합니다."
        return self

    def fails(self) -> bool:
        return bool(self.errors)

    def validate_or_fail(self) -> None:
        if self.fails():
            bad_request("입력값이 올바르지 않습니다.", self.errors)
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_validators.py -v` → Expected: PASS(6 tests).
- [ ] **Step 5: 전수 골든 보강** — `~/projects/SJMJ-Web/backend/tests/Unit/Core/ValidatorTest.php`의 15 케이스를 모두 동치로 포팅(체이닝, numeric, maxLength 멀티바이트, businessNumber 하이픈 등). Run 후 PASS 확인.
- [ ] **Step 6: Commit**

```bash
git add app/core/validators.py tests/unit/test_validators.py
git commit -m "feat(backend): Validator(fluent) — PHP 동치 15 골든 + 메시지 문자 일치"
```

---

## Task 5: invoice_repository.py — 실DB 골든(17 동치)

**Files:**
- Create: `app/repositories/__init__.py`, `app/repositories/invoice_repository.py`
- Test: `tests/integration/test_invoice_repository.py`

**Interfaces:**
- Consumes: `app.db.connection`(conn 바인딩), `sqlalchemy.text`.
- Produces: `class InvoiceRepository` 메서드 — `find_all(filters)->list[dict]`, `count_all(filters)->int`, `find_by_id(id)->dict|None`, `find_items(invoice_id)->list[dict]`, `insert(data)->int`, `insert_item(item)->None`, `update(id,data)->bool`, `delete_items(invoice_id)->None`, `delete(id)->bool`, `find_all_for_export(filters)->list[dict]`. 정렬 화이트리스트 `ALLOWED_SORT_COLUMNS={issue_date,grand_total,recipient,created_at}` / `ALLOWED_SORT_ORDERS={asc,desc}`.

- [ ] **Step 1: 실패 테스트** — `tests/integration/test_invoice_repository.py`(대표; 전수 17은 `~/projects/SJMJ-Web/backend/tests/Integration/Repositories/InvoiceRepositoryTest.php` 동치).

```python
import pytest
from app.repositories.invoice_repository import InvoiceRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _filters(**kw):
    base = {"page": 1, "limit": 20, "search": "", "date_from": "", "date_to": "",
            "sort_by": "issue_date", "sort_order": "desc"}
    base.update(kw)
    return base


def test_insert_and_find_by_id():
    repo = InvoiceRepository()
    new_id = repo.insert(td.invoice())
    row = repo.find_by_id(new_id)
    assert row["recipient"] == "한양운수"
    assert row["grand_total"] == 110000  # INT → int
    assert row["show_stamp"] == 1        # BOOLEAN → 1, not True


def test_insert_items_cascade_on_delete():
    repo = InvoiceRepository()
    iid = repo.insert(td.invoice())
    repo.insert_item({**td.invoice_item(), "invoice_id": iid, "item_order": 1})
    assert len(repo.find_items(iid)) == 1
    repo.delete(iid)
    assert repo.find_items(iid) == []     # FK ON DELETE CASCADE


def test_find_all_search_and_count():
    repo = InvoiceRepository()
    repo.insert(td.invoice({"recipient": "대성물류", "vehicle_no": "99바9999"}))
    repo.insert(td.invoice({"recipient": "한양운수"}))
    rows = repo.find_all(_filters(search="대성"))
    assert all("대성" in r["recipient"] for r in rows)
    assert repo.count_all(_filters(search="대성")) == 1


def test_sort_whitelist_rejects_injection():
    repo = InvoiceRepository()
    repo.insert(td.invoice())
    # 화이트리스트 밖 → issue_date로 보정(에러 없이 동작)
    rows = repo.find_all(_filters(sort_by="DROP TABLE", sort_order="evil"))
    assert isinstance(rows, list)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_invoice_repository.py -v` → Expected: FAIL(`InvoiceRepository` 없음). (실DB 연결도 이 단계에서 첫 검증 — `sjmj_test` MySQL 필요. 부재 시 conftest fixture가 연결 에러 → DB 준비 후 재실행.)

- [ ] **Step 3: 구현** — `app/repositories/__init__.py`(빈 파일), `app/repositories/invoice_repository.py`. PHP `InvoiceRepository.php` 1:1 이식.

```python
"""InvoiceRepository — PHP repositories/InvoiceRepository.php 동형(text() raw SQL)."""
from sqlalchemy import text

from app.db import connection

_ALLOWED_SORT_COLUMNS = {
    "issue_date": "i.issue_date",
    "grand_total": "i.grand_total",
    "recipient": "i.recipient",
    "created_at": "i.created_at",
}
_ALLOWED_SORT_ORDERS = {"asc", "desc"}


def _rows(result) -> list[dict]:
    return [dict(m) for m in result.mappings().all()]


class InvoiceRepository:
    def _where(self, filters: dict) -> tuple[str, dict]:
        where = "1=1"
        params: dict = {}
        if filters.get("search"):
            where += " AND (i.recipient LIKE :search1 OR i.vehicle_no LIKE :search2)"
            params["search1"] = f"%{filters['search']}%"
            params["search2"] = f"%{filters['search']}%"
        if filters.get("date_from"):
            where += " AND i.issue_date >= :date_from"
            params["date_from"] = filters["date_from"]
        if filters.get("date_to"):
            where += " AND i.issue_date <= :date_to"
            params["date_to"] = filters["date_to"]
        return where, params

    def find_all(self, filters: dict) -> list[dict]:
        where, params = self._where(filters)
        col = _ALLOWED_SORT_COLUMNS.get(filters["sort_by"], _ALLOWED_SORT_COLUMNS["issue_date"])
        order = filters["sort_order"] if filters["sort_order"] in _ALLOWED_SORT_ORDERS else "desc"
        params["limit"] = filters["limit"]
        params["offset"] = (filters["page"] - 1) * filters["limit"]
        sql = f"""
            SELECT i.id, i.document_title, i.issue_date, i.recipient, i.recipient2,
                   i.vehicle_no, i.memo, i.show_stamp, i.issuer_id,
                   i.total_supply, i.total_vat, i.grand_total, i.created_at, i.updated_at
            FROM invoices i
            WHERE {where}
            ORDER BY {col} {order}, i.id DESC
            LIMIT :limit OFFSET :offset
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))

    def count_all(self, filters: dict) -> int:
        where, params = self._where(filters)
        with connection() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM invoices i WHERE {where}"), params).scalar() or 0)

    def find_by_id(self, id: int) -> dict | None:
        with connection() as conn:
            row = conn.execute(text("SELECT * FROM invoices WHERE id = :id"), {"id": id}).mappings().first()
            return dict(row) if row else None

    def find_items(self, invoice_id: int) -> list[dict]:
        with connection() as conn:
            return _rows(conn.execute(
                text("SELECT * FROM invoice_items WHERE invoice_id = :id ORDER BY item_order"),
                {"id": invoice_id}))

    def insert(self, data: dict) -> int:
        with connection() as conn:
            result = conn.execute(text("""
                INSERT INTO invoices (document_title, issue_date, recipient, recipient2, vehicle_no,
                    memo, show_stamp, issuer_id, total_supply, total_vat, grand_total)
                VALUES (:document_title, :issue_date, :recipient, :recipient2, :vehicle_no,
                    :memo, :show_stamp, :issuer_id, :total_supply, :total_vat, :grand_total)
            """), {
                "document_title": data.get("document_title", "거 래 명 세 서"),
                "issue_date": data["issue_date"],
                "recipient": data["recipient"],
                "recipient2": data.get("recipient2", ""),
                "vehicle_no": data.get("vehicle_no", ""),
                "memo": data.get("memo"),
                "show_stamp": 1 if data.get("show_stamp", 1) else 0,
                "issuer_id": data.get("issuer_id"),
                "total_supply": data.get("total_supply", 0),
                "total_vat": data.get("total_vat", 0),
                "grand_total": data.get("grand_total", 0),
            })
            return int(result.lastrowid)

    def insert_item(self, item: dict) -> None:
        with connection() as conn:
            conn.execute(text("""
                INSERT INTO invoice_items (invoice_id, item_order, name, quantity, unit,
                    unit_price, supply, vat, total, deduction)
                VALUES (:invoice_id, :item_order, :name, :quantity, :unit,
                    :unit_price, :supply, :vat, :total, :deduction)
            """), {
                "invoice_id": item["invoice_id"],
                "item_order": item["item_order"],
                "name": item["name"],
                "quantity": item.get("quantity", 0),
                "unit": item.get("unit", "EA"),
                "unit_price": item.get("unit_price", 0),
                "supply": item.get("supply", 0),
                "vat": item.get("vat", 0),
                "total": item.get("total", 0),
                "deduction": 1 if item.get("deduction") else 0,
            })

    def update(self, id: int, data: dict) -> bool:
        with connection() as conn:
            result = conn.execute(text("""
                UPDATE invoices SET document_title=:document_title, issue_date=:issue_date,
                    recipient=:recipient, recipient2=:recipient2, vehicle_no=:vehicle_no,
                    memo=:memo, show_stamp=:show_stamp, issuer_id=:issuer_id,
                    total_supply=:total_supply, total_vat=:total_vat, grand_total=:grand_total
                WHERE id=:id
            """), {
                "id": id,
                "document_title": data.get("document_title", "거 래 명 세 서"),
                "issue_date": data["issue_date"],
                "recipient": data["recipient"],
                "recipient2": data.get("recipient2", ""),
                "vehicle_no": data.get("vehicle_no", ""),
                "memo": data.get("memo"),
                "show_stamp": 1 if data.get("show_stamp", 1) else 0,
                "issuer_id": data.get("issuer_id"),
                "total_supply": data.get("total_supply", 0),
                "total_vat": data.get("total_vat", 0),
                "grand_total": data.get("grand_total", 0),
            })
            return result.rowcount > 0

    def delete_items(self, invoice_id: int) -> None:
        with connection() as conn:
            conn.execute(text("DELETE FROM invoice_items WHERE invoice_id = :id"), {"id": invoice_id})

    def delete(self, id: int) -> bool:
        with connection() as conn:
            return conn.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": id}).rowcount > 0

    def find_all_for_export(self, filters: dict) -> list[dict]:
        where = "1=1"
        params: dict = {}
        if filters.get("date_from"):
            where += " AND i.issue_date >= :date_from"
            params["date_from"] = filters["date_from"]
        if filters.get("date_to"):
            where += " AND i.issue_date <= :date_to"
            params["date_to"] = filters["date_to"]
        if filters.get("company_id"):
            where += " AND i.recipient = (SELECT company_name FROM company_suggestions WHERE id = :company_id)"
            params["company_id"] = filters["company_id"]
        sql = f"""
            SELECT i.id, i.document_title, i.issue_date, i.recipient, i.recipient2,
                   i.vehicle_no, i.memo, i.total_supply, i.total_vat, i.grand_total, i.created_at
            FROM invoices i WHERE {where}
            ORDER BY i.issue_date DESC, i.id DESC
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))
```

> 주의: `connection()`이 tx 바인딩 conn을 재사용하므로, 테스트(db_conn fixture)의 단일 tx 안에서 insert→find가 같은 conn으로 보인다. `lastrowid`는 PyMySQL에서 INSERT 후 채워진다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_invoice_repository.py -v` → Expected: PASS.
- [ ] **Step 5: 전수 골든 보강** — PHP InvoiceRepositoryTest 17개(날짜필터·정렬 grand_total asc·countAll 필터·update·findAllForExport 날짜) 동치 포팅 후 PASS.
- [ ] **Step 6: Commit**

```bash
git add app/repositories/ tests/integration/test_invoice_repository.py
git commit -m "feat(backend): InvoiceRepository(text() SQL) — 실DB 골든 17 동치"
```

---

## Task 6: export_service.py — sanitizeCsvField + CSV 스트림(2 골든)

**Files:**
- Create: `app/services/__init__.py`, `app/services/export_service.py`
- Test: `tests/unit/test_export_service.py`

**Interfaces:**
- Consumes: `InvoiceRepository.find_all_for_export`.
- Produces: `sanitize_csv_field(value)->str`(static), `class ExportService(repo=None)` with `export_invoices(format, filters)->tuple[str, bytes]`(파일명, CSV 바이트). `format!='csv'`이면 `ValueError("현재 CSV 형식만 지원합니다.")`. 라우터가 이 바이트를 StreamingResponse로 흘린다.

> 설계 차이: PHP는 `php://output` 직접 출력. FastAPI는 (filename, csv_bytes)를 반환하고 라우터가 StreamingResponse로 보낸다(테스트 가능·envelope 밖).

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_export_service.py`.

```python
from app.services.export_service import sanitize_csv_field


def test_sanitize_blocks_formula_injection():
    assert sanitize_csv_field("=1+1") == "'=1+1"
    assert sanitize_csv_field("+82") == "'+82"
    assert sanitize_csv_field("-5") == "'-5"
    assert sanitize_csv_field("@x") == "'@x"


def test_sanitize_passes_safe_and_korean_and_none():
    assert sanitize_csv_field("한양운수") == "한양운수"
    assert sanitize_csv_field("12가3456") == "12가3456"
    assert sanitize_csv_field(None) == ""
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_export_service.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현** — `app/services/__init__.py`(빈), `app/services/export_service.py`.

```python
"""ExportService — PHP services/ExportService.php 동형(CSV + formula injection 방지)."""
import csv
import io
import re
from datetime import date

from app.repositories.invoice_repository import InvoiceRepository

_FORMULA = re.compile(r"^[=+\-@\t\r]")
_HEADER = ["ID", "문서제목", "발행일", "거래처", "거래처2", "차량번호", "메모", "공급가액", "부가세", "합계", "생성일"]


def sanitize_csv_field(value) -> str:
    s = "" if value is None else str(value)
    if s != "" and _FORMULA.match(s):
        return "'" + s
    return s


class ExportService:
    def __init__(self, repo: InvoiceRepository | None = None):
        self.repo = repo or InvoiceRepository()

    def export_invoices(self, format: str, filters: dict) -> tuple[str, bytes]:
        if format != "csv":
            raise ValueError("현재 CSV 형식만 지원합니다.")
        invoices = self.repo.find_all_for_export(filters)
        filename = f"거래명세서_{date.today().isoformat()}.csv"

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_HEADER)
        for inv in invoices:
            writer.writerow([
                inv["id"],
                sanitize_csv_field(inv["document_title"]),
                inv["issue_date"],
                sanitize_csv_field(inv["recipient"]),
                sanitize_csv_field(inv["recipient2"]),
                sanitize_csv_field(inv["vehicle_no"]),
                sanitize_csv_field(inv.get("memo") or ""),
                inv["total_supply"], inv["total_vat"], inv["grand_total"], inv["created_at"],
            ])
        # UTF-8 BOM(Excel 한글 호환)
        body = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
        return filename, body
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_export_service.py -v` → Expected: PASS(2 tests).
- [ ] **Step 5: Commit**

```bash
git add app/services/__init__.py app/services/export_service.py tests/unit/test_export_service.py
git commit -m "feat(backend): ExportService — sanitizeCsvField + UTF-8 BOM CSV(2 골든 동치)"
```

---

## Task 7: invoice_service.py — 비즈니스 로직(13 골든)

**Files:**
- Create: `app/services/invoice_service.py`
- Test: `tests/unit/test_invoice_service.py`

**Interfaces:**
- Consumes: `InvoiceRepository`, `CompanyRepository`·`ItemRepository`(`.increment_usage_by_name(name)` — 팬아웃 전엔 mock만; 슬라이스는 stub Protocol), `app.db.transaction`.
- Produces: `class InvoiceService(repo, company_repo, item_repo, *, transaction=db.transaction)` with `get_list(filters)`·`get_by_id(id)`·`create(data)`·`update(id,data)`·`delete(id)`·`duplicate(id)`. `get_list`→`{data,pagination:{page,limit,total,totalPages}}`(`totalPages=ceil(total/limit)`).

> 슬라이스 단계에선 `company_repo`/`item_repo`를 mock으로만 쓴다(골든 InvoiceServiceTest가 그렇게 함). 실제 Company/Item 리포는 팬아웃에서 구현. `increment_usage_by_name` 인터페이스만 약속한다.

- [ ] **Step 1: 실패 테스트** — `tests/unit/test_invoice_service.py`(PHP InvoiceServiceTest 13 동치; mock repo). 대표:

```python
import math
from contextlib import nullcontext
from unittest.mock import MagicMock, call

from app.services.invoice_service import InvoiceService
from tests.fixtures import test_data as td


def _svc(repo, company=None, item=None):
    return InvoiceService(repo, company or MagicMock(), item or MagicMock(), transaction=nullcontext)


def test_get_list_pagination():
    repo = MagicMock()
    repo.find_all.return_value = [td.invoice(), td.invoice({"recipient": "대성물류"})]
    repo.count_all.return_value = 25
    r = _svc(repo).get_list({"page": 2, "limit": 10, "sort_by": "issue_date", "sort_order": "desc"})
    assert r["pagination"]["total"] == 25
    assert r["pagination"]["totalPages"] == 3       # ceil(25/10)
    assert r["pagination"]["page"] == 2 and r["pagination"]["limit"] == 10


def test_get_by_id_attaches_items():
    repo = MagicMock()
    repo.find_by_id.return_value = {**td.invoice(), "id": 1}
    repo.find_items.return_value = [td.invoice_item(), td.invoice_item({"name": "브레이크오일"})]
    r = _svc(repo).get_by_id(1)
    assert r["items"][1]["name"] == "브레이크오일"


def test_get_by_id_none_skips_find_items():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert _svc(repo).get_by_id(42) is None
    repo.find_items.assert_not_called()


def test_create_inserts_invoice_items_and_usage():
    data = td.invoice_with_items()
    repo = MagicMock()
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {**data, "id": 1}
    repo.find_items.return_value = data["items"]
    company, item = MagicMock(), MagicMock()
    r = _svc(repo, company, item).create(data)
    assert r["id"] == 1
    assert repo.insert_item.call_count == 3
    company.increment_usage_by_name.assert_called_once_with("한양운수")
    assert item.increment_usage_by_name.call_count == 3


def test_create_without_recipient_skips_company_usage():
    data = td.invoice_with_items({"recipient": ""})
    repo = MagicMock(); repo.insert.return_value = 1
    repo.find_by_id.return_value = {**data, "id": 1}; repo.find_items.return_value = data["items"]
    company = MagicMock()
    _svc(repo, company).create(data)
    company.increment_usage_by_name.assert_not_called()


def test_update_returns_none_when_missing():
    repo = MagicMock(); repo.find_by_id.return_value = None
    assert _svc(repo).update(999, td.invoice_with_items()) is None


def test_update_replaces_items():
    data = td.invoice_with_items()
    repo = MagicMock()
    repo.find_by_id.return_value = {**data, "id": 1}
    repo.find_items.return_value = data["items"]
    _svc(repo).update(1, data)
    repo.update.assert_called_once()
    repo.delete_items.assert_called_once_with(1)
    assert repo.insert_item.call_count == 3


def test_duplicate_none_when_original_missing():
    repo = MagicMock(); repo.find_by_id.return_value = None
    assert _svc(repo).duplicate(999) is None
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_invoice_service.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현** — `app/services/invoice_service.py`. PHP `InvoiceService.php` 1:1.

```python
"""InvoiceService — PHP services/InvoiceService.php 동형."""
import math
from datetime import date

from app import db
from app.repositories.invoice_repository import InvoiceRepository


class InvoiceService:
    def __init__(self, repo=None, company_repo=None, item_repo=None, *, transaction=None):
        self.repo = repo or InvoiceRepository()
        self.company_repo = company_repo  # 팬아웃에서 CompanyRepository 주입
        self.item_repo = item_repo
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        data = self.repo.find_all(filters)
        total = self.repo.count_all(filters)
        return {
            "data": data,
            "pagination": {
                "page": filters["page"],
                "limit": filters["limit"],
                "total": total,
                "totalPages": math.ceil(total / filters["limit"]) if filters["limit"] else 0,
            },
        }

    def get_by_id(self, id: int) -> dict | None:
        invoice = self.repo.find_by_id(id)
        if not invoice:
            return None
        invoice["items"] = self.repo.find_items(id)
        return invoice

    def create(self, data: dict) -> dict | None:
        with self._transaction():
            invoice_id = self.repo.insert(data)
            for index, item in enumerate(data.get("items") or []):
                item = {**item, "item_order": index + 1, "invoice_id": invoice_id}
                self.repo.insert_item(item)
            self._update_usage_count(data)
        return self.get_by_id(invoice_id)

    def update(self, id: int, data: dict) -> dict | None:
        if not self.repo.find_by_id(id):
            return None
        with self._transaction():
            self.repo.update(id, data)
            self.repo.delete_items(id)
            for index, item in enumerate(data.get("items") or []):
                item = {**item, "item_order": index + 1, "invoice_id": id}
                self.repo.insert_item(item)
        return self.get_by_id(id)

    def delete(self, id: int) -> bool:
        return self.repo.delete(id)

    def duplicate(self, id: int) -> dict | None:
        original = self.get_by_id(id)
        if not original:
            return None
        new_data = {k: v for k, v in original.items() if k not in ("id", "created_at", "updated_at")}
        new_data["issue_date"] = date.today().isoformat()
        new_data["items"] = [{k: v for k, v in it.items() if k != "id"} for it in original.get("items", [])]
        return self.create(new_data)

    def _update_usage_count(self, data: dict) -> None:
        if data.get("recipient") and self.company_repo:
            self.company_repo.increment_usage_by_name(data["recipient"])
        if self.item_repo:
            for item in data.get("items") or []:
                if item.get("name"):
                    self.item_repo.increment_usage_by_name(item["name"])
```

> 골든 동치 주의: `test_create_*`는 company/item repo를 mock으로 주입하므로 `if self.company_repo` 가드가 호출을 막지 않는다. recipient=''이면 PHP `!empty`와 동일하게 skip.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_invoice_service.py -v` → Expected: PASS.
- [ ] **Step 5: 전수 골든 보강** — PHP 13개(duplicate 성공경로 포함) 동치 완료 후 PASS.
- [ ] **Step 6: Commit**

```bash
git add app/services/invoice_service.py tests/unit/test_invoice_service.py
git commit -m "feat(backend): InvoiceService — tx·usage·duplicate(13 골든 동치)"
```

---

## Task 8: routers/invoices.py + main 통합 — 계약 골든(19) + export 스트림

**Files:**
- Create: `app/routers/__init__.py`, `app/routers/invoices.py`, `app/models/__init__.py`, `app/models/invoice.py`(선택)
- Modify: `app/main.py`
- Test: `tests/contract/test_invoices_routes.py`

**Interfaces:**
- Consumes: `InvoiceService`, `ExportService`, envelope, errors, Validator.
- Produces: `router = APIRouter()` with 7 routes. `main.py`가 `register_error_handlers(app)` + `app.include_router(invoices.router, prefix="/api")`. **라우트 등록 순서: `/invoices/export`를 `/invoices/{id}`보다 먼저**.

- [ ] **Step 1: 실패 테스트** — `tests/contract/test_invoices_routes.py`(PHP InvoiceControllerTest 19 동치; TestClient + 실DB db_conn). 대표:

```python
import pytest
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _create(client, **ov):
    return client.post("/api/invoices", json=td.invoice_with_items(ov))


def test_store_creates_and_returns_201_structured(client):
    r = _create(client)
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["recipient"] == "한양운수"
    assert len(b["data"]["items"]) == 3


def test_store_validation_error_envelope(client):
    r = client.post("/api/invoices", json={"recipient": "x"})  # issue_date·items 누락
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False and b["error"]["code"] == "VALIDATION_ERROR"
    assert "issue_date" in b["error"]["details"] and "items" in b["error"]["details"]


def test_store_rejects_bad_date(client):
    r = _create(client, issue_date="2026/05/15")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_index_structured_pagination(client):
    _create(client)
    r = client.get("/api/invoices", params={"page": 1, "limit": 20})
    b = r.json()
    assert b["success"] is True
    assert isinstance(b["data"], list)
    assert set(b["pagination"]) == {"page", "limit", "total", "totalPages"}


def test_show_404_structured(client):
    r = client.get("/api/invoices/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_destroy_then_404(client):
    iid = _create(client).json()["data"]["id"]
    assert client.delete(f"/api/invoices/{iid}").json()["success"] is True
    assert client.delete(f"/api/invoices/{iid}").status_code == 404


def test_duplicate_201(client):
    iid = _create(client).json()["data"]["id"]
    r = client.post(f"/api/invoices/{iid}/duplicate")
    assert r.status_code == 201 and r.json()["data"]["id"] != iid


def test_export_csv_stream(client):
    _create(client)
    r = client.get("/api/invoices/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM


def test_export_bad_format_400(client):
    r = client.get("/api/invoices/export", params={"format": "pdf"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/contract/test_invoices_routes.py -v` → Expected: FAIL(라우터/앱 미구성).

- [ ] **Step 3: 구현 — 라우터** — `app/routers/__init__.py`(빈), `app/routers/invoices.py`. PHP `InvoiceController.php` 1:1(검증은 Validator, 응답은 envelope).

```python
"""invoices 라우터 — PHP controllers/InvoiceController.php 동형."""
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core import envelope
from app.core.errors import bad_request, not_found
from app.core.validators import Validator
from app.services.export_service import ExportService
from app.services.invoice_service import InvoiceService

router = APIRouter()


def _service() -> InvoiceService:
    return InvoiceService()


def _qint(req: Request, key: str, default: int) -> int:
    raw = req.query_params.get(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


@router.get("/invoices/export")
def export(request: Request):
    fmt = request.query_params.get("format", "csv")
    if fmt not in ("csv", "xlsx"):
        bad_request("format은 csv 또는 xlsx만 가능합니다.")
    filters = {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "company_id": request.query_params.get("company_id"),
    }
    try:
        filename, body = ExportService().export_invoices(fmt, filters)
    except ValueError as e:
        bad_request(str(e))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/invoices")
def index(request: Request):
    sort_by = request.query_params.get("sort_by", "issue_date")
    sort_order = request.query_params.get("sort_order", "desc")
    filters = {
        "page": max(_qint(request, "page", 1), 1),
        "limit": min(max(_qint(request, "limit", 20), 1), 100),
        "search": request.query_params.get("search"),
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "sort_by": sort_by if sort_by in ("issue_date", "grand_total", "recipient", "created_at") else "issue_date",
        "sort_order": sort_order if sort_order in ("asc", "desc") else "desc",
    }
    result = _service().get_list(filters)
    return envelope.list_response(result["data"], result["pagination"])


@router.get("/invoices/{id}")
def show(id: int):
    invoice = _service().get_by_id(id)
    if not invoice:
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.single(invoice)


@router.post("/invoices")
async def store(request: Request):
    data = await request.json()
    Validator().required(data, ["issue_date", "recipient"]) \
        .date_format(data, "issue_date") \
        .max_length(data, "recipient", 100) \
        .max_length(data, "vehicle_no", 255) \
        .non_empty_array(data, "items") \
        .validate_or_fail()
    return envelope.created(_service().create(data))


@router.put("/invoices/{id}")
async def update(id: int, request: Request):
    data = await request.json()
    Validator().required(data, ["issue_date", "recipient"]) \
        .date_format(data, "issue_date") \
        .max_length(data, "recipient", 100) \
        .max_length(data, "vehicle_no", 255) \
        .non_empty_array(data, "items") \
        .validate_or_fail()
    invoice = _service().update(id, data)
    if not invoice:
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.single(invoice)


@router.delete("/invoices/{id}")
def destroy(id: int):
    if not _service().delete(id):
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.deleted("거래명세서가 삭제되었습니다.")


@router.post("/invoices/{id}/duplicate")
def duplicate(id: int):
    new_invoice = _service().duplicate(id)
    if not new_invoice:
        not_found("원본 거래명세서를 찾을 수 없습니다.")
    return envelope.created(new_invoice)
```

> `/invoices/export`를 `/invoices/{id}`보다 먼저 정의해 `export`가 id로 오인되지 않게 한다(FastAPI는 선언 순서로 매칭). `id`를 `int`로 받아 비숫자 path는 자동 422가 아니라 — 주의: 골든은 숫자 id만 다루므로 OK. body 검증은 pydantic이 아니라 Validator로(골든 details 형태 보존).

- [ ] **Step 4: 구현 — main 통합** — `app/main.py`에 핸들러 등록 + 라우터 include(기존 health/static 유지).

```python
# main.py 상단 import에 추가
from app.core.errors import register_error_handlers
from app.routers import invoices

# app = FastAPI(...) 생성 직후
register_error_handlers(app)
app.include_router(invoices.router, prefix="/api")
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/contract/test_invoices_routes.py -v` → Expected: PASS.
- [ ] **Step 6: 전수 골든 보강** — PHP InvoiceControllerTest 19개(sort/order 기본·limit·page 0-클램프·update 404 등) 동치 완료. 전체 스위트 + 커버리지: `uv run pytest --cov=app --cov-report=term-missing` → invoices 슬라이스 80%↑ 확인.
- [ ] **Step 7: Commit**

```bash
git add app/routers/ app/main.py tests/contract/test_invoices_routes.py
git commit -m "feat(backend): invoices 7라우트(+export CSV/+duplicate) — 계약 골든 19 동치"
```

---

## Task 9: vertical slice 검증 게이트 (수동)

**Files:** 없음(검증).

- [ ] **Step 1: 전체 스위트** — Run: `uv run pytest --cov=app --cov-report=term-missing -v` → Expected: 전부 PASS, invoices 슬라이스 커버리지 80%↑.
- [ ] **Step 2: 무변경 프론트 스모크(선택, 1C 미리보기)** — `~/projects/SJMJ-Web/frontend`를 `VITE_API_MODE=modern` + `VITE_API_URL=http://localhost:8400/api`로 띄우고, FastAPI(`uv run uvicorn app.main:app --port 8400`) + 실DB에 붙여 목록/작성/수정/PDF 동작 확인. **확인 포인트**: invoices 목록 '건수'(C2 — `res.total` vs `pagination.total`). 깨지면 1C 핸드오프 §7-2(use-invoices 1줄 수정) 필요성 실증.
- [ ] **Step 3: 게이트 판정** — 4대 가정 반증 여부 기록: (a) 구조화 envelope로 프론트 동작, (b) export CSV envelope 밖 정상, (c) duplicate 전용 엔드포인트, (d) 실 DB tx 격리. 통과 시 팬아웃 착수.

---

## 팬아웃 (이 plan 범위 밖 — 별도 실행)

invoices 슬라이스가 게이트를 통과하면 나머지 5리소스(companies·items·settings·salespeople·sales-records)를 **dynamic workflow로 병렬 포팅**한다(로드맵 §2 도구 선택). 리소스당 1에이전트가 invoices 슬라이스가 확정한 패턴(envelope·Validator·repository text()·service tx·contract TestClient)을 재사용해 router/service/repository/models + contract/unit/integration 골든을 생성, 산출은 standard pipeline PR→리뷰→merge 게이트에 태운다. 리소스별 결정은 인벤토리 §4 + spec §5(DELETE `?id=`→path-param, items 중복 409, settings vat string, salespeople soft-delete/409 등)를 따른다. 이때 `TestData` 팩토리·`schema_test.sql`에 나머지 리소스 시드/팩토리를 보강한다.

---

## Self-Review

- **Spec 커버리지**: D1 envelope(Task 3) · D3 SQLAlchemy Core(Task 2,5) · D5 pydantic-settings(Task 1) · D7 Validator(Task 4) · 비-JSON export(Task 6,8) · 골든 동치 DoD(Task 5,7,8) · ML seam env 자리(Task 1) · 1C 핸드오프 실증(Task 9). invoices 30라우트 중 7 + export/duplicate 전부 매핑됨. 나머지 23라우트는 명시적으로 팬아웃으로 분리(working software 단위 = invoices 슬라이스).
- **Placeholder 스캔**: 각 구현 step에 실제 코드 포함. "전수 골든 보강" step은 PHP 골든 파일 경로를 정확히 지시(placeholder 아님 — 골든 파일이 곧 spec). 
- **타입 일관성**: `connection()`/`transaction()`(Task 2) ↔ repository 사용(Task 5) ↔ service `_transaction`(Task 7) 일치. `increment_usage_by_name`(Task 7에서 mock, 팬아웃에서 구현) 명시. envelope 함수명(`list_response`/`single`/`created`/`deleted`)이 Task 3 정의 ↔ Task 8 사용 일치. `bad_request`/`not_found`(Task 3) ↔ Validator/router(Task 4,8) 일치.

---

## Execution Handoff

Plan complete. 실행 옵션:
1. **Subagent-Driven(권장)** — task마다 fresh subagent, task 사이 리뷰.
2. **Inline Execution** — 이 세션에서 executing-plans로 체크포인트 배치 실행.

invoices 슬라이스는 로드맵상 "가정 반증" 목적이라 신중한 진행이 필요하다 — Task 0~4(foundation)는 inline으로 빠르게 세우고, Task 5~8(invoices)에서 게이트를 확인하는 혼합도 가능.
