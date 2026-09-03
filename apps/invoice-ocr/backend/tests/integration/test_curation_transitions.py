"""§6 불변식 — 배제 사유 축의 전이 폐쇄를 전수 고정한다(ADR 0006).

정적 2×2(ml의 is_machine_writable)만으로는 '사람이 되돌린 뒤 다시 배제'하는 경로가
고정되지 않는다. 그 전이의 결과 상태가 (excluded, NULL)이어야 기계가 영구히 손대지 않는다.
"""

import pytest
from sqlalchemy import text

from tests.fixtures.curation_helpers import job_token as _token

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_pair(engine, *, status="included", reason=None, job_status="done"):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_jobs (status, image_path) VALUES (:js, '/t.jpg')"),
            {"js": job_status},
        )
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
    return job_id, pair_id


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
    job_id, pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    assert _state(db_conn, pair_id) == ("excluded", "blank_crop")

    # 2) 사람이 포함으로 되돌림 — 사유는 유지된다(오탐 관측치 · 영구 보호)
    res = client.patch(
        f"/api/curation/pairs/{pair_id}", json={"status": "included", **_token(client, job_id)}
    )
    assert res.status_code == 200
    assert _state(db_conn, pair_id) == ("included", "blank_crop")

    # 3) 사람이 다시 배제 — 사유가 지워져 '사람이 배제'(첫 칸)로 간다
    res = client.patch(
        f"/api/curation/pairs/{pair_id}", json={"status": "excluded", **_token(client, job_id)}
    )
    assert res.status_code == 200
    assert _state(db_conn, pair_id) == ("excluded", None)
    # (excluded, NULL)은 ml의 is_machine_writable이 거부하는 칸이다 —
    # 그 술어의 전수 고정은 apps/invoice-ocr/ml/tests/test_blank_crop.py가 소유한다.


def test_patch_included_preserves_machine_reason(client, db_conn):
    job_id, pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    client.patch(
        f"/api/curation/pairs/{pair_id}", json={"status": "included", **_token(client, job_id)}
    )
    assert _state(db_conn, pair_id) == ("included", "blank_crop")


def test_patch_canonical_label_only_does_not_touch_reason(client, db_conn):
    job_id, pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")
    res = client.patch(
        f"/api/curation/pairs/{pair_id}",
        json={"canonical_label": "정식명", **_token(client, job_id)},
    )
    # PATCH가 실제로 성공하고 라벨을 바꿨는지부터 고정한다 — 이 단언이 없으면
    # 400/404/500으로 아무 일도 안 일어나도 기대 상태(= 시드 상태)가 그대로라 통과한다.
    assert res.status_code == 200
    assert res.json()["data"]["canonical_label"] == "정식명"
    assert _state(db_conn, pair_id) == ("excluded", "blank_crop")


def test_human_exclude_from_clean_state_keeps_reason_null(client, db_conn):
    job_id, pair_id = _seed_pair(db_conn)
    client.patch(
        f"/api/curation/pairs/{pair_id}", json={"status": "excluded", **_token(client, job_id)}
    )
    assert _state(db_conn, pair_id) == ("excluded", None)


@pytest.mark.parametrize("job_status", ["pending", "running", "failed"])
def test_pair_transitions_are_closed_while_the_job_is_not_done(client, db_conn, job_status):
    """done이 아닌 잡의 쌍은 어떤 전이도 받지 않는다 — 이 축의 칸이 비어 있었다(#94).

    commit_job이 그 잡의 쌍 전량을 재배치하므로 사이에 얹힌 사람의 결정은 경고 없이
    사라진다. 토큰만으로는 못 막는다 — 409 안내대로 새로고침하면 pending 잡의 **유효한**
    새 토큰이 손에 들어와 같은 PATCH가 통과하기 때문이다(상태 가드가 필요한 이유).

    거부 응답만 보지 않고 쌍의 상태·사유가 시드 그대로임을 함께 본다 — 409를 돌려주면서
    쓰기가 새는 경로는 status code만으로 잡히지 않는다.
    """
    job_id, pair_id = _seed_pair(
        db_conn, status="excluded", reason="blank_crop", job_status=job_status
    )
    token = _token(client, job_id)  # 시드 직후 조회라 토큰은 처음부터 최신이다

    res = client.patch(f"/api/curation/pairs/{pair_id}", json={"status": "included", **token})

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    assert _state(db_conn, pair_id) == ("excluded", "blank_crop")
