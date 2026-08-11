"""curation 슬라이스 계약 테스트 — 검수 큐 목록."""

import pytest
from sqlalchemy import text

from app.routers.curation import _LIMIT_MAX, _PAGE_MAX

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


def _token(client, job_id):
    """잡 상세에서 세대 토큰을 읽어 요청 body 조각으로 만든다(spec §12 — 필수 필드)."""
    return {"job_token": client.get(f"/api/curation/jobs/{job_id}").json()["data"]["job_token"]}


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
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "정식명", **_token(client, job_id)}
    )
    assert res.status_code == 200
    # 라벨만 고치는 PATCH는 사유를 건드리지 않는다.
    assert res.json()["data"]["exclusion_reason"] == "blank_crop"


def test_patch_pair_updates_canonical_label(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "정식명", **_token(client, job_id)}
    )
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "정식명"


def test_patch_pair_updates_status(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"status": "excluded", **_token(client, job_id)}
    )
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "excluded"


def test_patch_pair_empty_body_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(f"/api/curation/pairs/{pid}", json={**_token(client, job_id)})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "body" in res.json()["error"]["details"]  # model_validator 실패는 "body" 키(계약 고정)


def test_patch_pair_invalid_status_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"status": "garbage", **_token(client, job_id)}
    )
    assert res.status_code == 400


def test_patch_pair_404_when_missing(client, db_conn):
    res = client.patch("/api/curation/pairs/999999", json={"status": "excluded", "job_token": "0"})
    assert res.status_code == 404


def test_patch_pair_null_field_does_not_overwrite_status(client, db_conn):
    # Arrange — 시드 쌍의 status는 'included'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — status: null 명시, canonical_label만 실제 변경 값
    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"status": None, "canonical_label": "정상", **_token(client, job_id)},
    )
    # Assert — 500 아닌 200, status는 'included' 보존
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["canonical_label"] == "정상"
    assert data["status"] == "included"


def test_patch_pair_canonical_label_empty_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "", **_token(client, job_id)}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_pair_canonical_label_whitespace_only_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "   ", **_token(client, job_id)}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_pair_canonical_label_too_long_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "x" * 201, **_token(client, job_id)}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_pair_updates_canonical_label_preserves_status(client, db_conn):
    # Arrange — 시드 쌍의 status='included', canonical_label='품목'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — canonical_label만 변경
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "갱신명", **_token(client, job_id)}
    )
    # Assert — status는 원래 값 보존(exclude_unset 핵심 동작)
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "갱신명"
    assert res.json()["data"]["status"] == "included"


def test_patch_pair_updates_status_preserves_canonical_label(client, db_conn):
    # Arrange — 시드 쌍의 canonical_label='품목'
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    # Act — status만 변경
    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"status": "excluded", **_token(client, job_id)}
    )
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
        json={"status": "included", "exclusion_reason": "blank_crop", **_token(client, job_id)},
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
        json={"status": "excluded", "exclusion_reason": "blank_crop", **_token(client, job_id)},
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
        json={"status": "included", "exclusion_reason": "hacked", **_token(client, job_id)},
    )
    # Assert — 화이트리스트가 클라이언트 사유를 버리고, 포함 방향은 사유를 지우지도 않는다.
    assert res.status_code == 200
    assert _reason_in_db(db_conn, pid) == "blank_crop"


def test_patch_pair_with_only_exclusion_reason_is_400(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"exclusion_reason": "blank_crop", **_token(client, job_id)},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


# ── POST /api/curation/jobs/{job_id}/review ────────────────────────────────


def test_review_marks_job_and_stamps_unreviewed_pairs(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=2, unreviewed=2)
    res = client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
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

    assert (
        client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id)).status_code
        == 200
    )
    after_first = _stamps(db_conn, job_id)
    # 모든 쌍이 검수됨 + 이미 찍힌 row 0은 sentinel 그대로(덮어쓰기 방지 입증).
    assert all(ts is not None for ts in after_first)
    assert str(after_first[0]) == sentinel

    # 2차 호출도 멱등 — 이미 찍힌 값은 불변.
    assert (
        client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id)).status_code
        == 200
    )
    after_second = _stamps(db_conn, job_id)
    assert str(after_second[0]) == sentinel
    assert after_first == after_second


def test_review_404_when_missing(client, db_conn):
    res = client.post("/api/curation/jobs/999999/review", json={"job_token": "0"})
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

    res = client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))

    assert res.status_code == 200
    assert res.json() == {"success": True, "data": {"job_id": job_id, "curation_reviewed": True}}
    names = _suggestion_names(db_conn)
    assert "정식품목" in names
    assert "품목" not in names  # final_label은 등록 대상이 아니다


def test_review_is_idempotent_for_the_dictionary(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    first = client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
    second = client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
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


def _pair_status(engine, pair_id):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT status FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()


def test_patch_pair_releases_gate_of_reviewed_job(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
    held_flag, first_stamp = _gate(db_conn, job_id)
    # 전제 단언 — 게이트가 실제로 걸렸고 첫 검수 시각이 찍혔다. 이게 없으면 mark_reviewed가
    # 퇴행해 전 상태가 (0, None)이어도 아래 flag == 0 / stamp == first_stamp가 둘 다
    # 통과해, "걸렸다가 풀렸다"를 증명하지 못한 채 GREEN이 된다.
    assert held_flag == 1
    assert first_stamp is not None

    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "수정라벨", **_token(client, job_id)}
    )

    assert res.status_code == 200
    assert res.json()["data"]["job_curation_reviewed"] is False
    flag, stamp = _gate(db_conn, job_id)
    assert flag == 0  # 게이트 해제
    assert stamp == first_stamp  # 첫 검수 시각은 유지 → "재검수 필요"로 판별된다

    job = next(j for j in client.get("/api/curation/jobs").json()["data"] if j["job_id"] == job_id)
    assert job["unreviewed_count"] == 1  # 게이트 해제와 재확인 대상 수가 함께 움직인다


def test_patch_pair_on_unreviewed_job_applies_edit_and_reports_false(client, db_conn):
    """이미 미검수인 잡은 0 → 0 no-op이며 응답도 False다(spec §3.4). 수정 자체는 반영된다.

    시드가 이미 (0, None)이라 게이트 단언만으로는 "수정이 실제로 처리됐다"가 증명되지
    않는다 — status 전이(included → excluded)를 함께 고정해 no-op인 것은 게이트뿐임을
    분명히 한다. curation_reviewed_at이 NULL로 남는 단언은 유지한다(release_gate가
    스탬프를 찍기 시작하면 "미검수"가 "재검수 필요"로 오분류되므로 여기서 걸려야 한다).
    """
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)

    res = client.patch(
        f"/api/curation/pairs/{pid}", json={"status": "excluded", **_token(client, job_id)}
    )

    assert res.status_code == 200
    assert res.json()["data"]["job_curation_reviewed"] is False
    assert res.json()["data"]["status"] == "excluded"  # 수정은 반영됐다
    assert _pair_status(db_conn, pid) == "excluded"
    assert _gate(db_conn, job_id) == (0, None)


def test_patch_pair_does_not_register_after_job_reviewed(client, db_conn):
    """검수완료 후 라벨을 고쳐도 사전에 등록되지 않는다 — 재확정이 유일한 등록 트리거."""
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))

    res = client.patch(
        f"/api/curation/pairs/{pid}",
        json={"canonical_label": "검수후라벨", **_token(client, job_id)},
    )
    assert res.status_code == 200
    assert "검수후라벨" not in _suggestion_names(db_conn)

    # 재확정하면 그때 등록된다.
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
    assert "검수후라벨" in _suggestion_names(db_conn)


def test_re_review_after_gate_release_keeps_first_review_stamp(client, db_conn):
    """해제 → 재확정 왕복 후에도 API가 노출하는 첫 검수 시각은 그대로다(COALESCE).

    "재검수 필요"(해제됐지만 과거에 검수된 잡)와 "미검수"(한 번도 검수 안 한 잡)를 가르는
    유일한 근거가 HTTP 경로 끝까지 살아 있는지 고정한다.

    첫 값을 과거 sentinel로 **직접 심는다**. 두 번 검수하는 방식은 curation_reviewed_at이
    소수초 0자리라 같은 초 안에서는 COALESCE를 빼고 `= CURRENT_TIMESTAMP`로 구현해도
    통과한다(false-green).
    """
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET curation_reviewed_at = '2020-01-01 00:00:00' WHERE id = :id"),
            {"id": job_id},
        )

    client.patch(
        f"/api/curation/pairs/{pid}", json={"canonical_label": "수정라벨", **_token(client, job_id)}
    )
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))

    job = next(j for j in client.get("/api/curation/jobs").json()["data"] if j["job_id"] == job_id)
    assert job["curation_reviewed_at"] == "2020-01-01T00:00:00"


def test_list_jobs_exposes_curation_reviewed_at(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))

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
    client.post(f"/api/curation/jobs/{job_id}/review", json=_token(client, job_id))

    res = client.get(f"/api/curation/jobs/{job_id}")

    assert res.json()["data"]["curation_reviewed_at"] is not None


def test_patch_pair_validation_error_envelope_is_unchanged(client, db_conn):
    """외부 계약 불변식 — 필드 추가가 에러 envelope·status·details 형태를 건드리지 않는다."""
    job_id = _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)
    pid = _first_pair_id(db_conn, job_id)

    res = client.patch(f"/api/curation/pairs/{pid}", json={**_token(client, job_id)})

    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    details = body["error"]["details"]
    # 비어 있지 않음을 먼저 요구한다 — 빈 dict면 아래 all(...)이 공허하게 참이 되어
    # "details는 {필드: 메시지} 문자열 맵"이라는 불변식이 사실상 고정되지 않는다.
    assert isinstance(details, dict) and details
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in details.items())


# ---------------------------------------------------------------------------
# POST /api/curation/jobs/{job_id}/reprocess (spec §10)
# ---------------------------------------------------------------------------


def _seed_job(engine, *, status="done", result_json='{"rows": []}'):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json) VALUES (:s, '/x.jpg', :r)"
            ),
            {"s": status, "r": result_json},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _job_row(engine, job_id):
    with engine.begin() as conn:
        return (
            conn.execute(
                text("SELECT status, result_json FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            )
            .mappings()
            .first()
        )


def test_reprocess_moves_a_done_job_back_to_pending(client, db_conn):
    job_id = _seed_job(db_conn)

    res = client.post(f"/api/curation/jobs/{job_id}/reprocess")

    assert res.status_code == 200
    assert res.json() == {"success": True, "data": {"job_id": job_id, "status": "pending"}}
    assert _job_row(db_conn, job_id)["status"] == "pending"


def test_reprocess_allows_a_done_job_that_was_never_confirmed(client, db_conn):
    """확정 증거가 없는 done 잡도 200이다 — 모집단 가드를 두지 않는 것이 의도다(spec §10).

    확정 전 잡은 training_pairs가 없어 승계가 no-op이 되고(spec §1) 초안 갱신 + 크롭 교체만
    일어난다 — "신규 잡을 새 엔진으로 다시 돌린 것"과 동치라 오염 통로가 아니다. 등록 전에
    크롭이 나쁜 걸 발견했을 때 쓸 수 있어야 하므로 열어 둔다. 재처리 **대상 모집단**을
    확정 잡으로 좁히는 것은 런북의 책임이고, 그 술어는 ocr_repository._UNCONFIRMED_WHERE의
    부정이다(런북 §2).
    """
    job_id = _seed_job(db_conn)  # invoice_id NULL · correction·pair 없음

    res = client.post(f"/api/curation/jobs/{job_id}/reprocess")

    assert res.status_code == 200
    assert _job_row(db_conn, job_id)["status"] == "pending"


def test_reprocess_preserves_result_json(client, db_conn):
    """result_json은 재처리 판별의 근거이자 실패 시 롤백 대상이다(spec §10)."""
    job_id = _seed_job(db_conn, result_json='{"rows": [{"row_index": 0}]}')

    res = client.post(f"/api/curation/jobs/{job_id}/reprocess")

    assert res.status_code == 200
    assert '"row_index"' in _job_row(db_conn, job_id)["result_json"]


def test_reprocess_returns_404_for_unknown_job(client):
    res = client.post("/api/curation/jobs/999999/reprocess")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_reprocess_returns_409_when_job_is_already_queued(client, db_conn):
    """이미 running/pending인 잡의 중복 요청을 막는다."""
    job_id = _seed_job(db_conn, status="running")

    res = client.post(f"/api/curation/jobs/{job_id}/reprocess")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    assert _job_row(db_conn, job_id)["status"] == "running"


def test_reprocess_returns_409_for_a_failed_job(client, db_conn):
    job_id = _seed_job(db_conn, status="failed")

    assert client.post(f"/api/curation/jobs/{job_id}/reprocess").status_code == 409


def test_reprocess_never_touches_the_confirmed_invoice(client, db_conn):
    """불변식 4 — 확정된 거래명세서는 재처리가 건드리지 않는다.

    OcrService.confirm의 `invoice_id is not None` 가드가 재확정을 막는데, 그 가드의 입력이
    바로 ocr_jobs.invoice_id다. 재처리가 이 링크를 지우면 가드가 뚫려 같은 잡이 두 번째
    거래명세서를 만든다 — 링크와 invoices 행이 그대로임을 고정한다.
    """
    with db_conn.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO invoices (issue_date, recipient, total_supply) "
                "VALUES ('2026-08-01', '거래처', 1000)"
            )
        )
        invoice_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json, invoice_id) "
                "VALUES ('done', '/x.jpg', '{\"rows\": []}', :inv)"
            ),
            {"inv": invoice_id},
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()

    res = client.post(f"/api/curation/jobs/{job_id}/reprocess")

    assert res.status_code == 200
    with db_conn.begin() as conn:
        linked = conn.execute(
            text("SELECT invoice_id FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
        survives = conn.execute(
            text("SELECT COUNT(*) FROM invoices WHERE id = :id"), {"id": invoice_id}
        ).scalar()
    assert linked == invoice_id
    assert survives == 1


# ---------------------------------------------------------------------------
# GET /api/curation/jobs/{job_id} — 미결 쌍은 새 행과 조인되지 않는다 (spec §6-1)
# ---------------------------------------------------------------------------


def test_job_detail_exposes_crop_available_for_orphaned_pairs(client, db_conn):
    """미결 쌍의 UI 계약 — crop_available=false + 빈 top5(§6-1)."""
    with db_conn.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json) VALUES "
                "('done', '/x.jpg', '{\"rows\": [{\"row_index\": 0, "
                '"item_top5": [{"label": "무", "sim": 0.9}], "item_uncertain": true}]}\')'
            )
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        for ref, row in ((f"job-{job_id}/row-0", 0), (f"job-{job_id}/orphan-77", 0)):
            conn.execute(
                text(
                    "INSERT INTO training_pairs "
                    "(crop_ref, job_id, row_index, final_label, canonical_label, supply, status, "
                    "exclusion_reason) VALUES (:r, :j, :i, '품목', '품목', 1000, 'included', NULL)"
                ),
                {"r": ref, "j": job_id, "i": row},
            )

    pairs = client.get(f"/api/curation/jobs/{job_id}").json()["data"]["pairs"]

    assert [p["crop_available"] for p in pairs] == [True, False]
    assert pairs[1]["top5"] == []
    assert pairs[1]["uncertain"] is False


# ---------------------------------------------------------------------------
# 낙관적 잠금 (spec §12)
# ---------------------------------------------------------------------------


def _push_token_forward(engine, job_id):
    """updated_at을 1초 **앞으로** 밀어 세대 토큰을 결정론적으로 벌린다(spec §12).

    updated_at은 초 단위라 같은 초 안에서 상태를 전이하면 토큰이 전이 전과 같아진다.
    UPDATE 문이 updated_at을 **명시 지정**하면 ON UPDATE CURRENT_TIMESTAMP가 발동하지
    않으므로 여기서 쓴 값이 그대로 남는다 — 그래서 이 호출은 그 테스트의 **마지막 쓰기**
    여야 한다(앞에 두면 뒤따르는 쓰기가 NOW로 되돌려 무효가 된다).

    뒤가 아니라 앞으로 미는 이유: 중간 쓰기가 초 경계를 넘었다면 현재 값이 이미
    stale + 1초라, 거기서 1초를 빼면 정확히 stale에 착지해 테스트가 거꾸로 실패한다.
    앞으로 밀면 현재 값이 stale이든 stale + 1이든 결과가 stale과 절대 같지 않다.
    """
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = updated_at + INTERVAL 1 SECOND WHERE id = :id"),
            {"id": job_id},
        )


def test_job_detail_carries_a_job_token(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn)

    token = client.get(f"/api/curation/jobs/{job_id}").json()["data"]["job_token"]

    assert isinstance(token, str) and token


def test_patch_pair_requires_a_job_token(client, db_conn):
    """토큰 없는 PATCH는 400 — 계약을 옵션으로 두면 방어가 없는 클라이언트가 살아남는다."""
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)

    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "job_token" in res.json()["error"]["details"]


def test_patch_pair_rejects_a_stale_token_with_409(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)

    res = client.patch(
        f"/api/curation/pairs/{pair_id}",
        json={"canonical_label": "휠", "job_token": "0"},
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_reprocess_invalidates_an_open_editors_token(client, db_conn):
    """재처리는 status를 전이하므로 updated_at이 반드시 튄다 — 새 컬럼이 필요 없다(§12)."""
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)
    stale = _token(client, job_id)
    client.post(f"/api/curation/jobs/{job_id}/reprocess")
    _push_token_forward(db_conn, job_id)  # 마지막 쓰기여야 한다 — 헬퍼 docstring 참조

    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠", **stale})

    assert res.status_code == 409
    # 옛 화면이 심으려던 라벨이 실제로 반영되지 않았는지 DB로 확인한다 — 409만 보면
    # 거부 응답만 확인하고 쓰기가 새는 경로는 못 잡는다.
    with db_conn.begin() as conn:
        label = conn.execute(
            text("SELECT canonical_label FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()
    assert label == "품목"


def test_patch_pair_rejects_edits_on_a_job_queued_for_reprocess(client, db_conn):
    """재처리 큐에 든 잡의 쌍은 **유효한 최신 토큰으로도** 고칠 수 없다.

    세대 토큰만으로는 이 경로를 못 막는다 — 409 메시지가 "새로고침한 뒤 다시 시도하세요"라고
    직접 안내하고, 새로고침하면 pending 잡의 유효한 새 토큰이 손에 들어와 같은 PATCH가
    통과한다. 그 사이 워커의 commit_job이 쌍 전량을 재배치하므로 사람의 결정이 경고 없이
    사라진다. mark_reviewed와 같은 상태 가드를 둬야 방어가 대칭이 된다.
    """
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)
    client.post(f"/api/curation/jobs/{job_id}/reprocess")
    fresh = _token(client, job_id)  # 사람이 안내대로 새로고침해 받은 유효한 토큰

    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠", **fresh})

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    with db_conn.begin() as conn:
        label = conn.execute(
            text("SELECT canonical_label FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()
    assert label == "품목", "거부 응답만 보면 쓰기가 새는 경로를 못 잡는다"


def test_patch_pair_response_carries_the_refreshed_token(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)

    res = client.patch(
        f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠", **_token(client, job_id)}
    )

    assert res.status_code == 200
    assert isinstance(res.json()["data"]["job_token"], str)


def test_the_refreshed_token_lets_the_editor_continue_without_reloading(client, db_conn):
    """연속 편집 — 응답 토큰으로 곧바로 다음 PATCH가 통과해야 한다(Task 8이 이 왕복에 의존).

    게이트가 걸린 잡(reviewed=1)으로 시작해야 첫 PATCH의 release_gate가 값을 실제로 바꿔
    updated_at이 튄다(같은 값 UPDATE는 ON UPDATE CURRENT_TIMESTAMP를 발동시키지 않는다).
    시드의 updated_at을 1초 **뒤로** 밀어 그 튐이 초 경계를 반드시 넘게 만든다 — 여기서는
    앞선 stale 테스트와 의도가 반대라 방향도 반대다. 같은 초에 머물면 쓰기 **이전**
    토큰을 돌려주는 구현도 통과해(false-green) 연속 편집 회귀를 못 잡는다.
    """
    job_id = _seed_job_with_pairs(db_conn, reviewed=1, pairs=1, unreviewed=1)
    pair_id = _first_pair_id(db_conn, job_id)
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = updated_at - INTERVAL 1 SECOND WHERE id = :id"),
            {"id": job_id},
        )

    first = client.patch(
        f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠", **_token(client, job_id)}
    )
    second = client.patch(
        f"/api/curation/pairs/{pair_id}",
        json={"canonical_label": "타이어", "job_token": first.json()["data"]["job_token"]},
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["data"]["canonical_label"] == "타이어"


def test_review_requires_a_job_token(client, db_conn):
    """토큰 없는 review는 400 — PATCH만 막으면 게이트를 닫는 쪽에 구멍이 남는다."""
    job_id = _seed_job_with_pairs(db_conn)

    res = client.post(f"/api/curation/jobs/{job_id}/review", json={})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "job_token" in res.json()["error"]["details"]


def test_review_rejects_a_stale_token_with_409(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn)

    res = client.post(f"/api/curation/jobs/{job_id}/review", json={"job_token": "0"})

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_stale_review_after_reprocess_cannot_close_the_gate(client, db_conn):
    """재처리로 열린 게이트를 옛 화면의 검수 완료가 다시 닫지 못한다(§7 · §11-1).

    통과시키면 사람이 새 미결 쌍을 보지 않은 채 --reembed-job 가드를 통과시킨다.
    """
    job_id = _seed_job_with_pairs(db_conn)
    stale = _token(client, job_id)
    client.post(f"/api/curation/jobs/{job_id}/reprocess")
    # 재처리 요청 직후의 잡은 pending이므로 상태 가드에도 걸린다 — done으로 되돌려
    # 토큰 대조만을 단독으로 검증한다(워커가 커밋을 끝낸 시점의 모사).
    with db_conn.begin() as conn:
        conn.execute(text("UPDATE ocr_jobs SET status = 'done' WHERE id = :id"), {"id": job_id})
    _push_token_forward(db_conn, job_id)  # 마지막 쓰기여야 한다 — 헬퍼 docstring 참조

    res = client.post(f"/api/curation/jobs/{job_id}/review", json=stale)

    assert res.status_code == 409
    with db_conn.begin() as conn:
        gate = conn.execute(
            text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert gate == 0, "게이트가 열린 채 남아 재검수가 강제된다"


def test_review_rejects_a_job_that_is_not_done(client, db_conn):
    """재처리 큐에 들어간 잡의 검수 완료는 곧 덮어써질 사실이다."""
    job_id = _seed_job_with_pairs(db_conn)
    with db_conn.begin() as conn:
        conn.execute(text("UPDATE ocr_jobs SET status = 'pending' WHERE id = :id"), {"id": job_id})
    token = _token(client, job_id)  # 상태 전이 뒤에 읽어 토큰은 최신이다

    assert client.post(f"/api/curation/jobs/{job_id}/review", json=token).status_code == 409


def test_patch_pair_rejects_a_blank_job_token_as_a_validation_error(client, db_conn):
    """빈 토큰은 400이다 — 통과시키면 형식 오류가 409(세대 충돌)로 둔갑한다.

    409는 "새로고침하면 낫는다"는 뜻인데 이 경우는 새로고침해도 낫지 않고, 로그에서도
    진짜 세대 충돌과 구분되지 않는다.
    """
    job_id = _seed_job_with_pairs(db_conn)
    pair_id = _first_pair_id(db_conn, job_id)

    res = client.patch(
        f"/api/curation/pairs/{pair_id}", json={"canonical_label": "휠", "job_token": "   "}
    )

    assert res.status_code == 400
    assert "job_token" in res.json()["error"]["details"]


def test_review_rejects_a_blank_job_token_as_a_validation_error(client, db_conn):
    job_id = _seed_job_with_pairs(db_conn)

    res = client.post(f"/api/curation/jobs/{job_id}/review", json={"job_token": ""})

    assert res.status_code == 400
    assert "job_token" in res.json()["error"]["details"]


def test_list_jobs_clamps_absurdly_large_page_without_500(client, db_conn):
    """page 상한 부재로 offset=(page-1)*limit이 MySQL BIGINT 범위를 넘겨 500 + SQL 전문
    노출이 나던 경로를 닫는다(ocr 라우터 test_list_unconfirmed_jobs_clamps_absurdly_large_page_without_500의 미러).

    상한을 넘는 page는 400이 아니라 clamp된다 — 무음 clamp 의미론을 그대로 유지한다.
    """
    _seed_job_with_pairs(db_conn, reviewed=0, pairs=1, unreviewed=1)

    # 진짜 불변식은 offset=(page-1)*limit이 BIGINT 범위 안이라는 것이므로 최악 조합
    # (page 상한 × limit 상한)으로 찌른다 — page만 키우면 offset이 상한에 못 미친다.
    res = client.get(f"/api/curation/jobs?page=99999999999999999999999&limit={_LIMIT_MAX}")

    assert res.status_code == 200
    body = res.json()
    # 상한(<=)만 보면 page가 1로 무너지는 회귀도 통과한다 — 그러면 offset이 0이 되어
    # 아래 잡이 딸려 나온다. 등치 + 빈 data 두 단언이 함께 그 회귀를 막는다.
    assert body["pagination"]["page"] == _PAGE_MAX
    assert body["pagination"]["limit"] == _LIMIT_MAX
    assert body["data"] == []
    # conftest가 테스트마다 전체 TRUNCATE로 격리하고 위에서 잡 1건만 시드했으므로
    # total은 결정적으로 1이다 — `>= 1`이면 total이 잡 수가 아닌 학습쌍 수로 바뀌는
    # 회귀를 통과시킨다.
    assert body["pagination"]["total"] == 1
