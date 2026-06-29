"""실DB 골든 하니스 — sjmj_test MySQL + truncate 격리 + TestClient.

격리 방식: 모듈 전역 엔진(스레드 공유)을 테스트 엔진으로 교체하고, 테스트마다
모든 테이블을 TRUNCATE + app_settings 재시드한다. (contextvar 롤백 대신 truncate를
쓰는 이유: TestClient의 sync 엔드포인트는 threadpool에서 돌아 contextvar 기반
롤백 격리가 통하지 않을 수 있기 때문. 전역 엔진은 스레드 공유라 엔드포인트가
같은 테스트 DB를 쓴다.)
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import db as dbmod

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
_APP_SETTINGS_SEED = [
    ("default_vat_rate", "0.1"),
    ("default_document_title", "거 래 명 세 서"),
    ("default_unit", "EA"),
    ("pdf_filename_pattern", "거래명세서_{recipient}_{issue_date}"),
]


def _test_url() -> str:
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ.get("DB_NAME", "sjmj_test")
    user = os.environ.get("DB_USER", "sjmj_test")
    pw = os.environ.get("DB_PASS", "sjmj_test_pass")
    return f"mysql+pymysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"


@pytest.fixture(scope="session")
def _engine():
    engine = create_engine(_test_url(), pool_pre_ping=True, future=True)
    schema = (Path(__file__).parent / "fixtures" / "schema_test.sql").read_text()
    with engine.begin() as conn:
        for stmt in schema.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    yield engine
    engine.dispose()


def _reset(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in _ALL_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {t}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        for k, v in _APP_SETTINGS_SEED:
            conn.execute(
                text(
                    "INSERT INTO app_settings (setting_key, setting_value) VALUES (:k, :v)"
                ),
                {"k": k, "v": v},
            )


@pytest.fixture
def db_conn(_engine):
    """테스트 엔진 주입 + 클린 테이블(truncate). 값으로 엔진을 돌려준다."""
    dbmod.set_test_engine(_engine)
    _reset(_engine)
    yield _engine
    dbmod.reset_engine()


@pytest.fixture
def client(db_conn):
    from app.main import app

    return TestClient(app)
