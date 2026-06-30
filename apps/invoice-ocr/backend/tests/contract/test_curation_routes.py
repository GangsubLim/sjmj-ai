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


# ── PATCH /api/curation/pairs/{id} ─────────────────────────────────────────


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


def test_patch_pair_null_field_does_not_overwrite_status(client, db_conn):
    # Arrange — 시드 쌍의 status는 'included'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — status: null 명시, canonical_label만 실제 변경 값
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"status": None, "canonical_label": "정상"}
    )
    # Assert — 500 아닌 200, status는 'included' 보존
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["canonical_label"] == "정상"
    assert data["status"] == "included"


def test_patch_pair_canonical_label_empty_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": ""})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_pair_canonical_label_too_long_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "x" * 201})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_pair_updates_canonical_label_preserves_status(client, db_conn):
    # Arrange — 시드 쌍의 status='included', canonical_label='품목'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — canonical_label만 변경
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "갱신명"})
    # Assert — status는 원래 값 보존(exclude_unset 핵심 동작)
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "갱신명"
    assert res.json()["data"]["status"] == "included"


def test_patch_pair_updates_status_preserves_canonical_label(client, db_conn):
    # Arrange — 시드 쌍의 canonical_label='품목'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — status만 변경
    res = client.patch(f"/api/curation/pairs/{pid}", json={"status": "excluded"})
    # Assert — canonical_label은 '품목' 보존
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "excluded"
    assert res.json()["data"]["canonical_label"] == "품목"


# ── POST /api/curation/jobs/{job_id}/review ────────────────────────────────


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
    # 미검수 쌍이 있는 잡 시드 → 1차 검수 완료
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=2, unreviewed=2)
    res = client.post(f"/api/curation/jobs/{job_id}/review")
    assert res.status_code == 200

    # 1차 직후 reviewed_at 보관
    with db_conn.begin() as conn:
        first_stamps = (
            conn.execute(
                text("SELECT reviewed_at FROM training_pairs WHERE job_id = :id ORDER BY id ASC"),
                {"id": job_id},
            )
            .scalars()
            .all()
        )

    # 2차 호출 — reviewed_at IS NULL 가드로 덮어쓰기 방지
    res = client.post(f"/api/curation/jobs/{job_id}/review")
    assert res.status_code == 200

    # reviewed_at이 NULL이 아니고 1차와 동일 (덮어써지지 않음)
    with db_conn.begin() as conn:
        second_stamps = (
            conn.execute(
                text("SELECT reviewed_at FROM training_pairs WHERE job_id = :id ORDER BY id ASC"),
                {"id": job_id},
            )
            .scalars()
            .all()
        )

    assert all(ts is not None for ts in first_stamps)
    assert first_stamps == second_stamps


def test_review_404_when_missing(client, db_conn):
    res = client.post("/api/curation/jobs/999999/review")
    assert res.status_code == 404
