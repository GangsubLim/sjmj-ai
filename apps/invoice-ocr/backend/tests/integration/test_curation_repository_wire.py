"""CurationRepository의 배제 판정이 schemas.curation의 공유 상수를 실제로 참조하는지 검증한다.

repository가 "excluded" 문자열을 독자적으로 하드코딩하면, 화이트리스트 값이 바뀔 때
이 사본만 stale로 남아 exclusion_reason 삭제 조건이 조용히 fail-open된다(리뷰 M1).
같은 상수 객체를 참조해야 값이 바뀌면 양쪽이 함께 반응한다 — monkeypatch로 그 반응을 증명한다.
"""

import pytest
from sqlalchemy import text

from app.repositories import curation_repository
from app.repositories.curation_repository import CurationRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_pair(engine, *, status="excluded", reason="blank_crop"):
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
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _reason(engine, pair_id):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT exclusion_reason FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()


def test_update_pair_excluded_check_uses_shared_status_excluded_constant(monkeypatch, db_conn):
    """repository의 배제 판정 상수를 다른 값으로 바꾸면 실제 UPDATE 동작도 따라 바뀌어야 한다.

    이 테스트가 repository 모듈에 STATUS_EXCLUDED 속성이 없으면 monkeypatch.setattr가
    AttributeError로 실패한다(기본 raising=True) — 즉 이 테스트 자체가 "상수를 공유해서
    참조하라"는 요구를 강제한다.
    """
    monkeypatch.setattr(curation_repository, "STATUS_EXCLUDED", "다른값")
    pair_id = _seed_pair(db_conn, status="excluded", reason="blank_crop")

    CurationRepository().update_pair(pair_id, {"status": "excluded"})

    # 상수를 참조했다면 "다른값" != "excluded"라 사유가 지워지지 않아야 한다.
    # (하드코딩된 리터럴 비교였다면 monkeypatch와 무관하게 항상 지워진다.)
    assert _reason(db_conn, pair_id) == "blank_crop"
