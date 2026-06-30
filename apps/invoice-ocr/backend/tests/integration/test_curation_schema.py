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
        row = (
            conn.execute(
                text(
                    "SELECT crop_ref, status, canonical_label, reviewed_at FROM training_pairs WHERE job_id = :j"
                ),
                {"j": job_id},
            )
            .mappings()
            .first()
        )
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
