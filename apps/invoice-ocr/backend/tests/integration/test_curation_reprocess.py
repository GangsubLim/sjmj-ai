"""재처리 전이의 repository 계약 — 실 MySQL."""

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed(engine, status="done"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json) "
                "VALUES (:s, '/x.jpg', '{\"rows\": []}')"
            ),
            {"s": status},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def test_find_job_for_update_reads_current_status(db_conn):
    job_id = _seed(db_conn, "done")

    assert CurationRepository().find_job_for_update(job_id) == {"id": job_id, "status": "done"}


def test_find_job_for_update_returns_none_for_unknown_job():
    assert CurationRepository().find_job_for_update(999999) is None


def test_requeue_for_reprocess_keeps_result_json(db_conn):
    job_id = _seed(db_conn, "done")

    CurationRepository().requeue_for_reprocess(job_id)

    with db_conn.begin() as conn:
        row = (
            conn.execute(
                text("SELECT status, result_json FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            )
            .mappings()
            .first()
        )
    assert row["status"] == "pending"
    assert row["result_json"] is not None
