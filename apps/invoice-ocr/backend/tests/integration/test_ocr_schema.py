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
