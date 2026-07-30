"""§6 불변식 — 배제 사유 축의 전이 폐쇄를 전수 고정한다(ADR 0006).

정적 2×2(ml의 is_machine_writable)만으로는 '사람이 되돌린 뒤 다시 배제'하는 경로가
고정되지 않는다. 그 전이의 결과 상태가 (excluded, NULL)이어야 기계가 영구히 손대지 않는다.
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_pair(engine, *, status="included", reason=None):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/t.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status, exclusion_reason) "
                "VALUES (:r, :j, 0, '품목', '품목', :s, :e)"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id, "s": status, "e": reason},
        )
        pair_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    return pair_id


def _state(engine, pair_id):
    with engine.begin() as conn:
        row = (
            conn.execute(
                text("SELECT status, exclusion_reason FROM training_pairs WHERE id = :id"),
                {"id": pair_id},
            )
            .mappings()
            .first()
        )
    return (row["status"], row["exclusion_reason"])


def test_full_transition_closure_machine_exclude_human_include_human_exclude(client, db_conn):
    # 1) 기계가 배제한 상태에서 출발
    pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    assert _state(db_conn, pair_id) == ("excluded", "blank_crop")

    # 2) 사람이 포함으로 되돌림 — 사유는 유지된다(오탐 관측치 · 영구 보호)
    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"status": "included"})
    assert res.status_code == 200
    assert _state(db_conn, pair_id) == ("included", "blank_crop")

    # 3) 사람이 다시 배제 — 사유가 지워져 '사람이 배제'(첫 칸)로 간다
    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"status": "excluded"})
    assert res.status_code == 200
    assert _state(db_conn, pair_id) == ("excluded", None)
    # (excluded, NULL)은 ml의 is_machine_writable이 거부하는 칸이다 —
    # 그 술어의 전수 고정은 apps/invoice-ocr/ml/tests/test_blank_crop.py가 소유한다.


def test_patch_included_preserves_machine_reason(client, db_conn):
    pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    client.patch(f"/api/curation/pairs/{pair_id}", json={"status": "included"})
    assert _state(db_conn, pair_id) == ("included", "blank_crop")


def test_patch_canonical_label_only_does_not_touch_reason(client, db_conn):
    pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"canonical_label": "정식명"})
    # PATCH가 실제로 성공하고 라벨을 바꿨는지부터 고정한다 — 이 단언이 없으면
    # 400/404/500으로 아무 일도 안 일어나도 기대 상태(= 시드 상태)가 그대로라 통과한다.
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "정식명"
    assert _state(db_conn, pair_id) == ("excluded", "blank_crop")


def test_human_exclude_from_clean_state_keeps_reason_null(client, db_conn):
    pair_id = _seed_pair(db_conn)
    client.patch(f"/api/curation/pairs/{pair_id}", json={"status": "excluded"})
    assert _state(db_conn, pair_id) == ("excluded", None)
