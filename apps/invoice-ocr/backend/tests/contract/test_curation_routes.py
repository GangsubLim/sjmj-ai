"""curation 슬라이스 계약 테스트 — 검수 큐 목록."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job_with_pairs(engine, *, reviewed=0, pairs=2, unreviewed=2, canonical="품목"):
    """ocr_jobs 1건 + training_pairs N건 시드. job_id 반환.

    canonical을 넘기면 final_label('품목')과 정식 라벨을 벌릴 수 있다 — 두 값이 같으면
    "정식 라벨을 등록한다"와 "final_label을 등록한다"를 단언으로 구분할 수 없다.
    """
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
                    f"VALUES (:r, :j, :i, '품목', :c, 1000, 'included', {stamped})"
                ),
                {"r": f"job-{job_id}/row-{i}", "j": job_id, "i": i, "c": canonical},
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


def test_job_detail_pair_uncertain_reflects_item_uncertain_flag(client, db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/x.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        # result_json의 crop_ref를 실제 job_id로 채운다 — 'job-1'을 하드코딩하면 TRUNCATE로
        # AUTO_INCREMENT가 리셋돼 job_id가 우연히 1인 것에 기대게 되고, 같은 테스트에 잡을
        # 하나 더 시드하는 순간 training_pairs.crop_ref와 어긋난다(조인 키 오독 유발).
        conn.execute(
            text("UPDATE ocr_jobs SET result_json = :rj WHERE id = :id"),
            {
                "id": job_id,
                "rj": (
                    '{"rows": ['
                    f'{{"row_index": 0, "crop_ref": "job-{job_id}/row-0", "item_top5": [], '
                    '"item_uncertain": true, "supply": 100000}, '
                    f'{{"row_index": 1, "crop_ref": "job-{job_id}/row-1", "item_top5": [], '
                    '"supply": 100000}], '
                    '"supply_sum": 200000, "warp_ok": true}'
                ),
            },
        )
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, final_label, canonical_label, "
                "supply, status) "
                "VALUES (:r, :j, 0, '삼겹살', '목살', '목살', 100000, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, final_label, canonical_label, "
                "supply, status) "
                "VALUES (:r, :j, 1, '삼겹살', '목살', '목살', 100000, 'included')"
            ),
            {"r": f"job-{job_id}/row-1", "j": job_id},
        )
    res = client.get(f"/api/curation/jobs/{job_id}")
    assert res.status_code == 200
    assert res.json()["success"] is True
    pairs = {p["row_index"]: p for p in res.json()["data"]["pairs"]}
    # item_uncertain: true인 행 → uncertain True
    assert pairs[0]["uncertain"] is True
    # 플래그가 없는 과거 잡 행 → uncertain False(하위호환)
    assert pairs[1]["uncertain"] is False


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


def _set_exclusion(engine, pair_id, *, status, reason):
    """기계 판정 상태를 직접 심는다(API로는 사유를 쓸 수 없으므로 SQL로 시드)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE training_pairs SET status = :s, exclusion_reason = :r WHERE id = :id"),
            {"s": status, "r": reason, "id": pair_id},
        )


def test_job_detail_pairs_include_exclusion_reason(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    _set_exclusion(db_conn, pid, status="excluded", reason="blank_crop")
    res = client.get(f"/api/curation/jobs/{job_id}")
    assert res.status_code == 200
    assert res.json()["data"]["pairs"][0]["exclusion_reason"] == "blank_crop"


def test_job_detail_pair_exclusion_reason_is_null_by_default(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}")
    assert res.json()["data"]["pairs"][0]["exclusion_reason"] is None


def test_patch_pair_response_includes_exclusion_reason(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    _set_exclusion(db_conn, pid, status="excluded", reason="blank_crop")
    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "정식명"})
    assert res.status_code == 200
    # 라벨만 고치는 PATCH는 사유를 건드리지 않는다.
    assert res.json()["data"]["exclusion_reason"] == "blank_crop"


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


def _reason_in_db(engine, pair_id):
    """응답 echo가 아니라 DB 실값으로 사유를 읽는다(화이트리스트 실검증)."""
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT exclusion_reason FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()


def test_patch_pair_ignores_client_sent_exclusion_reason(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # status는 included 유지 + 사유를 심으려는 시도 — 사유는 무시된다(기계만 채운다).
    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"status": "included", "exclusion_reason": "blank_crop"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["exclusion_reason"] is None


def test_patch_pair_client_sent_reason_cannot_forge_machine_exclusion(client, db_conn):
    # Arrange — 사유가 비어 있는 정상 후보 (included, NULL)
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — 배제하면서 기계 사유를 함께 밀어넣으려는 시도(위험 방향)
    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"status": "excluded", "exclusion_reason": "blank_crop"},
    )
    # Assert — 사유는 NULL로 남아 '사람이 배제'로 기록된다. 여기서 사유가 심어지면
    # 사람의 배제가 기계 배제로 위조돼 이후 기계가 사람 판정을 되돌린다(ADR 0006 §6).
    assert res.status_code == 200
    assert _reason_in_db(db_conn, pid) is None


def test_patch_pair_client_sent_reason_does_not_overwrite_machine_reason(client, db_conn):
    # Arrange — 기계가 심은 사유가 있는 상태
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    _set_exclusion(db_conn, pid, status="excluded", reason="blank_crop")
    # Act — 포함으로 되돌리면서 사유를 클라이언트 값으로 덮으려는 시도
    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"status": "included", "exclusion_reason": "hacked"},
    )
    # Assert — 화이트리스트가 클라이언트 사유를 버리고, 포함 방향은 사유를 지우지도 않는다.
    assert res.status_code == 200
    assert _reason_in_db(db_conn, pid) == "blank_crop"


def test_patch_pair_with_only_exclusion_reason_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={"exclusion_reason": "blank_crop"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


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


# ── GET /api/curation/jobs/{id}/image/{kind} ───────────────────────────────


@pytest.fixture
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    return tmp_path


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # 최소 PNG 시그니처


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


def test_image_404_when_job_missing(client, db_conn, _data_dir):
    # 존재하지 않는 잡 — job_exists 가드가 파일 조회 전에 404를 낸다.
    res = client.get("/api/curation/jobs/999999/image/original")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_image_blocks_path_traversal_via_kind(client, db_conn, _data_dir, tmp_path):
    # kind는 enum(original|warped) — 경로 조작 토큰은 422→400 검증 에러로 거부된다.
    outside = tmp_path.parent / "outside-secret.png"
    outside.write_bytes(b"SECRET-OUTSIDE-DATA-ROOT")
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    res = client.get(f"/api/curation/jobs/{job_id}/image/%2e%2e")  # 디코드 시 ".." 단일 세그먼트
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert b"SECRET-OUTSIDE-DATA-ROOT" not in res.content


# ── 정식 라벨 → 자동완성 사전 등록 배선(#40 spec §3.4) ──────────────────────


def _suggestion_names(engine) -> list[str]:
    with engine.begin() as conn:
        return list(conn.execute(text("SELECT item_name FROM item_suggestions")).scalars())


def test_review_registers_included_labels_end_to_end(client, db_conn):
    """라우터가 ItemRepository를 실제로 주입했는지 — 배선 회귀 방어선.

    시드의 final_label('품목')과 canonical_label('정식품목')을 벌려, 사전에 들어가는
    값이 사람이 확정한 정식 라벨임을 단언한다(둘이 같으면 구분 불가).
    """
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1, canonical="정식품목")

    res = client.post(f"/api/curation/jobs/{job_id}/review")

    assert res.status_code == 200
    assert res.json() == {"success": True, "data": {"job_id": job_id, "curation_reviewed": True}}
    names = _suggestion_names(db_conn)
    assert "정식품목" in names
    assert "품목" not in names  # final_label은 등록 대상이 아니다


def test_review_is_idempotent_for_the_dictionary(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    first = client.post(f"/api/curation/jobs/{job_id}/review")
    second = client.post(f"/api/curation/jobs/{job_id}/review")
    # 2차 호출도 200이어야 한다 — ensure_exists가 평범한 INSERT로 회귀하면 여기서 깨진다.
    assert (first.status_code, second.status_code) == (200, 200)
    assert _suggestion_names(db_conn).count("품목") == 1


# ── 게이트 해제(Issue #52) ─────────────────────────────────────────────────


def _gate(engine, job_id):
    with engine.begin() as conn:
        row = (
            conn.execute(
                text("SELECT curation_reviewed, curation_reviewed_at FROM ocr_jobs WHERE id = :id"),
                {"id": job_id},
            )
            .mappings()
            .first()
        )
    return (row["curation_reviewed"], row["curation_reviewed_at"])


def test_patch_pair_releases_gate_of_reviewed_job(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/review")
    _flag, first_stamp = _gate(db_conn, job_id)

    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "수정라벨"})

    assert res.status_code == 200
    assert res.json()["data"]["job_curation_reviewed"] is False
    flag, stamp = _gate(db_conn, job_id)
    assert flag == 0  # 게이트 해제
    assert stamp == first_stamp  # 첫 검수 시각은 유지 → "재검수 필요"로 판별된다

    job = next(j for j in client.get("/api/curation/jobs").json()["data"] if j["job_id"] == job_id)
    assert job["unreviewed_count"] == 1  # 게이트 해제와 재확인 대상 수가 함께 움직인다


def test_patch_pair_on_unreviewed_job_reports_false(client, db_conn):
    """이미 미검수인 잡은 0 → 0 no-op이며 응답도 False다(spec §3.4)."""
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)

    res = client.patch(f"/api/curation/pairs/{pid}", json={"status": "excluded"})

    assert res.status_code == 200
    assert res.json()["data"]["job_curation_reviewed"] is False
    assert _gate(db_conn, job_id) == (0, None)


def test_patch_pair_does_not_register_after_job_reviewed(client, db_conn):
    """검수완료 후 라벨을 고쳐도 사전에 등록되지 않는다 — 재확정이 유일한 등록 트리거."""
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/review")

    res = client.patch(f"/api/curation/pairs/{pid}", json={"canonical_label": "검수후라벨"})
    assert res.status_code == 200
    assert "검수후라벨" not in _suggestion_names(db_conn)

    # 재확정하면 그때 등록된다.
    client.post(f"/api/curation/jobs/{job_id}/review")
    assert "검수후라벨" in _suggestion_names(db_conn)


def test_list_jobs_exposes_curation_reviewed_at(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    client.post(f"/api/curation/jobs/{job_id}/review")

    res = client.get("/api/curation/jobs")

    job = next(j for j in res.json()["data"] if j["job_id"] == job_id)
    assert job["curation_reviewed_at"] is not None


def test_list_jobs_curation_reviewed_at_is_null_for_never_reviewed(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)

    res = client.get("/api/curation/jobs")

    job = next(j for j in res.json()["data"] if j["job_id"] == job_id)
    assert job["curation_reviewed_at"] is None


def test_job_detail_exposes_curation_reviewed_at(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    client.post(f"/api/curation/jobs/{job_id}/review")

    res = client.get(f"/api/curation/jobs/{job_id}")

    assert res.json()["data"]["curation_reviewed_at"] is not None


def test_patch_pair_validation_error_envelope_is_unchanged(client, db_conn):
    """외부 계약 불변식 — 필드 추가가 에러 envelope·status·details 형태를 건드리지 않는다."""
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)

    res = client.patch(f"/api/curation/pairs/{pid}", json={})

    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["details"], dict)
    assert all(isinstance(v, str) for v in body["error"]["details"].values())
