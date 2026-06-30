"""curation 슬라이스 계약 테스트 — 검수 큐 목록."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job_with_pairs(engine, *, reviewed=0, pairs=2, unreviewed=2):
    """ocr_jobs 1건 + training_pairs N건 시드. job_id 반환."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) VALUES ('done', '/x.jpg', :r)"
            ),
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
        conn.execute(
            text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/no-pairs.jpg')")
        )
        empty_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    res = client.get("/api/curation/jobs")
    assert all(j["job_id"] != empty_id for j in res.json()["data"])


def test_list_jobs_orders_unreviewed_first(client, db_conn):
    # Arrange — 검수완료 잡 먼저 삽입해 DB 삽입 순서와 정렬 순서가 다름을 보장
    reviewed_id = _seed_job_with_pairs(db_conn, reviewed=1, pairs=1, unreviewed=0)
    unreviewed_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)

    # Act
    res = client.get("/api/curation/jobs")

    # Assert — 미검수(False) 잡이 검수완료(True) 잡보다 앞에 위치해야 한다
    ids = [j["job_id"] for j in res.json()["data"]]
    assert unreviewed_id in ids
    assert reviewed_id in ids
    assert ids.index(unreviewed_id) < ids.index(reviewed_id)


def test_list_jobs_pagination_meta(client, db_conn):
    # Arrange
    _seed_job_with_pairs(db_conn, reviewed=0, pairs=2, unreviewed=2)

    # Act — 기본 파라미터(page=1, limit=20)
    res = client.get("/api/curation/jobs")

    # Assert — pagination 키·타입·기본값
    pagination = res.json()["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 20
    assert isinstance(pagination["total"], int) and pagination["total"] >= 1
    assert isinstance(pagination["totalPages"], int) and pagination["totalPages"] >= 1

    # limit 상한 클램프: 500 요청 → 응답 limit == 100
    clamped = client.get("/api/curation/jobs?limit=500")
    assert clamped.json()["pagination"]["limit"] == 100
