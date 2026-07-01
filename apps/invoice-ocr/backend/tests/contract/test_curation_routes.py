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


def test_patch_pair_canonical_label_whitespace_only_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "   "})
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


def _stamps(db_conn, job_id):
    """job의 training_pairs reviewed_at을 id순으로 반환."""
    with db_conn.begin() as conn:
        return (
            conn.execute(
                text("SELECT reviewed_at FROM training_pairs WHERE job_id = :id ORDER BY id ASC"),
                {"id": job_id},
            )
            .scalars()
            .all()
        )


def test_review_is_idempotent(client, db_conn):
    # row 0을 과거 시각으로 이미 검수 처리 → 가드가 이 값을 덮지 않아야 함.
    # (TIMESTAMP 1초 해상도 탓에 단순 "1차==2차" 비교는 가드가 없어도 통과하므로,
    #  구별 가능한 sentinel을 심어 reviewed_at IS NULL 가드를 직접 입증한다.)
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=2, unreviewed=2)
    sentinel = "2020-01-01 00:00:00"
    with db_conn.begin() as conn:
        conn.execute(
            text(
                "UPDATE training_pairs SET reviewed_at = :ts WHERE job_id = :id AND row_index = 0"
            ),
            {"ts": sentinel, "id": job_id},
        )

    assert client.post(f"/api/curation/jobs/{job_id}/review").status_code == 200
    after_first = _stamps(db_conn, job_id)
    # 모든 쌍이 검수됨 + 이미 찍힌 row 0은 sentinel 그대로(덮어쓰기 방지 입증).
    assert all(ts is not None for ts in after_first)
    assert str(after_first[0]) == sentinel

    # 2차 호출도 멱등 — 이미 찍힌 값은 불변.
    assert client.post(f"/api/curation/jobs/{job_id}/review").status_code == 200
    after_second = _stamps(db_conn, job_id)
    assert str(after_second[0]) == sentinel
    assert after_first == after_second


def test_review_404_when_missing(client, db_conn):
    res = client.post("/api/curation/jobs/999999/review")
    assert res.status_code == 404


# ── GET /api/curation/jobs/{id}/image/{kind} + /crop/{row} ─────────────────


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
    assert res.headers["content-type"] == "image/png"  # 확장자 기반 media_type 추정 고정


def test_warped_image_returns_png(client, db_conn, _data_dir):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    crop_dir = _data_dir / "ocr_crops" / f"job-{job_id}"
    crop_dir.mkdir(parents=True)
    (crop_dir / "warped.png").write_bytes(_PNG_BYTES)

    res = client.get(f"/api/curation/jobs/{job_id}/image/warped")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == _PNG_BYTES


def test_original_image_404_when_file_missing(client, db_conn, _data_dir):
    # image_path가 가리키는 파일이 디스크에 없는 잡 — 200이 아니라 404.
    with db_conn.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/nonexistent/x.png')")
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    res = client.get(f"/api/curation/jobs/{job_id}/image/original")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_image_and_crop_404_when_job_missing(client, db_conn, _data_dir):
    # 존재하지 않는 잡 — job_exists 가드가 파일 조회 전에 404를 낸다.
    for path in ("image/original", "crop/0"):
        res = client.get(f"/api/curation/jobs/999999/{path}")
        assert res.status_code == 404, path
        assert res.json()["error"]["code"] == "NOT_FOUND"


def test_crop_blocks_path_traversal_via_row(client, db_conn, _data_dir, tmp_path):
    # SJMJ_DATA_DIR(=tmp_path) 밖에 민감 파일을 두고, row로 도달 불가함을 실증한다.
    outside = tmp_path.parent / "outside-secret.png"
    outside.write_bytes(b"SECRET-OUTSIDE-DATA-ROOT")
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)

    # row는 int 타입 — 경로 조작 토큰은 422→400 검증 에러로 거부되어
    # SJMJ_DATA_DIR/ocr_crops/job-{id}/row-{int}.png 밖으로 절대 벗어날 수 없다.
    # (%2e%2e는 서버에서 ".."로 디코드되지만 단일 세그먼트라 row int 파싱에서 거부된다.)
    for evil in ("%2e%2e", "row-0.png", "..%2e"):
        res = client.get(f"/api/curation/jobs/{job_id}/crop/{evil}")
        assert res.status_code == 400, f"traversal token not rejected: {evil!r}"
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert b"SECRET-OUTSIDE-DATA-ROOT" not in res.content


def test_image_blocks_path_traversal_via_kind(client, db_conn, _data_dir, tmp_path):
    # kind는 enum(original|warped) — 경로 조작 토큰은 422→400 검증 에러로 거부된다.
    outside = tmp_path.parent / "outside-secret.png"
    outside.write_bytes(b"SECRET-OUTSIDE-DATA-ROOT")
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}/image/%2e%2e")  # 디코드 시 ".." 단일 세그먼트
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert b"SECRET-OUTSIDE-DATA-ROOT" not in res.content
