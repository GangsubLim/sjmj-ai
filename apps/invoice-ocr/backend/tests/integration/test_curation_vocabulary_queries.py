"""CurationRepository의 어휘 동기 입력 조회 2종(#40 spec §3.3)."""

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job(engine, *, reviewed=0):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/v.jpg', :r)"
            ),
            {"r": reviewed},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _seed_pair(engine, job_id, row_index, label, status="included"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                "VALUES (:r, :j, :i, :l, :l, :s)"
            ),
            {
                "r": f"job-{job_id}/row-{row_index}",
                "j": job_id,
                "i": row_index,
                "l": label,
                "s": status,
            },
        )


def test_list_included_labels_returns_only_included_in_row_order(db_conn):
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 2, "라이닝1조")
    _seed_pair(db_conn, job_id, 0, "휠")
    _seed_pair(db_conn, job_id, 1, "제외품목", status="excluded")

    assert CurationRepository().list_included_labels(job_id) == ["휠", "라이닝1조"]


def test_list_included_labels_scoped_to_job(db_conn):
    mine = _seed_job(db_conn)
    other = _seed_job(db_conn)
    _seed_pair(db_conn, mine, 0, "내잡라벨")
    _seed_pair(db_conn, other, 0, "남의잡라벨")

    assert CurationRepository().list_included_labels(mine) == ["내잡라벨"]


def test_list_included_labels_drops_null_but_keeps_blank(db_conn):
    """NULL은 SQL이 거르고, 빈 문자열·공백은 그대로 넘긴다(정규화는 service 책임)."""
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, None)
    _seed_pair(db_conn, job_id, 1, "")
    _seed_pair(db_conn, job_id, 2, "  ")
    _seed_pair(db_conn, job_id, 3, "정상라벨")

    assert CurationRepository().list_included_labels(job_id) == ["", "  ", "정상라벨"]


def test_list_included_labels_empty_for_unknown_job(db_conn):
    assert CurationRepository().list_included_labels(999999) == []


def test_is_job_reviewed_true_when_flagged(db_conn):
    assert CurationRepository().is_job_reviewed(_seed_job(db_conn, reviewed=1)) is True


def test_is_job_reviewed_false_when_not_flagged(db_conn):
    assert CurationRepository().is_job_reviewed(_seed_job(db_conn, reviewed=0)) is False


def test_is_job_reviewed_false_for_unknown_job(db_conn):
    assert CurationRepository().is_job_reviewed(999999) is False
