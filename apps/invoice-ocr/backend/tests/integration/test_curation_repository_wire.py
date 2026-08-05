"""CurationRepository의 배제 판정이 schemas.curation의 공유 상수를 실제로 참조하는지 검증한다.

repository가 "excluded" 문자열을 독자적으로 하드코딩하면, 화이트리스트 값이 바뀔 때
이 사본만 stale로 남아 exclusion_reason 삭제 조건이 조용히 fail-open된다(리뷰 M1).
같은 상수 객체를 참조해야 값이 바뀌면 양쪽이 함께 반응한다 — monkeypatch로 그 반응을 증명한다.
release_gate/mark_reviewed의 게이트 해제·재검수 타임스탬프 왕복도 여기서 함께 검증한다.
"""

import importlib

import pytest
from sqlalchemy import text

from app.repositories import curation_repository
from app.repositories.curation_repository import CurationRepository
from app.schemas import curation as curation_schema

pytestmark = pytest.mark.usefixtures("db_conn")

# 시드·요청 값은 진실원(schemas.curation)에서 읽는다 — 리터럴로 박으면 화이트리스트 값이
# 바뀔 때 repository가 정상인데도 아래 양성 대조가 깨진다(false RED).
_EXCLUDED = curation_schema.STATUS_EXCLUDED
_INCLUDED = curation_schema.STATUS_INCLUDED


def _seed_pair(engine, *, status=_EXCLUDED, reason="blank_crop"):
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
    # 양성 대조 — 실제 상수값에서는 같은 호출이 사유를 반드시 지운다.
    # (이 단언이 없으면 update_pair의 `exclusion_reason = NULL` 분기를 통째로 지워도
    #  아래 음성 케이스만으로는 GREEN이 유지돼 테스트가 무력해진다.)
    control_id = _seed_pair(db_conn, status=_EXCLUDED, reason="blank_crop")
    CurationRepository().update_pair(control_id, {"status": _EXCLUDED})
    assert _reason(db_conn, control_id) is None

    monkeypatch.setattr(curation_repository, "STATUS_EXCLUDED", "다른값")
    pair_id = _seed_pair(db_conn, status=_EXCLUDED, reason="blank_crop")

    CurationRepository().update_pair(pair_id, {"status": _EXCLUDED})

    # 상수를 참조했다면 "다른값" != "excluded"라 사유가 지워지지 않아야 한다.
    # (하드코딩된 리터럴 비교였다면 monkeypatch와 무관하게 항상 지워진다.)
    assert _reason(db_conn, pair_id) == "blank_crop"


def test_list_included_labels_uses_shared_status_included_constant(monkeypatch, db_conn):
    """list_included_labels의 WHERE status 필터가 공유 STATUS_INCLUDED 상수를 참조하는지 검증한다."""
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/t.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs (crop_ref, job_id, row_index, final_label, "
                "canonical_label, status) VALUES (:r, :j, 0, '초안', '품목', :status)"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id, "status": _INCLUDED},
        )

    # 양성 대조 — 실제 상수값이면 라벨이 반환된다.
    assert CurationRepository().list_included_labels(job_id) == ["품목"]

    monkeypatch.setattr(curation_repository, "STATUS_INCLUDED", "다른값")

    # 상수를 참조했다면 "다른값" != "included"라 매칭이 사라져 빈 리스트가 반환돼야 한다.
    assert CurationRepository().list_included_labels(job_id) == []


def test_repository_status_constants_are_bound_from_schemas_module():
    """repository의 상수가 schemas.curation에서 **가져온** 것인지 고정한다.

    위 두 테스트는 "값을 모듈 전역에서 호출 시점에 조회한다"까지만 증명한다 — repository가
    자기 모듈에 `STATUS_EXCLUDED = "excluded"` 사본을 정의해도 monkeypatch가 그 사본을
    덮으므로 그대로 통과한다. 진실원 쪽 값을 바꾼 뒤 repository를 재로딩해 값이 따라오는지
    보면 사본이 걸린다(짧은 ASCII 문자열은 인터닝돼 `is` 비교로는 구분되지 않는다).

    monkeypatch를 쓰지 않는 이유: 복구는 teardown이 아니라 **재로딩 직전**에 일어나야
    모듈 전역이 원래 값으로 되돌아온다.
    """
    original = curation_schema.STATUS_EXCLUDED
    try:
        curation_schema.STATUS_EXCLUDED = "다른값"
        assert importlib.reload(curation_repository).STATUS_EXCLUDED == "다른값"
    finally:
        curation_schema.STATUS_EXCLUDED = original
        importlib.reload(curation_repository)
    assert original == curation_repository.STATUS_EXCLUDED  # 복구 확인(다음 테스트 오염 방지)


def _seed_job_and_pair(engine, *, reviewed=0):
    """검수 상태를 지정한 잡 + 스탬프 찍힌 쌍 1건. (job_id, pair_id) 반환."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/g.jpg', :r)"
            ),
            {"r": reviewed},
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status, reviewed_at) "
                "VALUES (:r, :j, 0, '품목', '품목', :status, '2026-01-01 09:00:00')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id, "status": _INCLUDED},
        )
        pair_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    return job_id, pair_id


def test_update_pair_clears_reviewed_at(db_conn):
    """사람이 수정한 쌍은 재확인 대상으로 되돌아간다(spec §3.1).

    ml/tools/blank_crop_report.py 의 기계 경로가 쓰는 관례와 같다 — 두 경로가
    reviewed_at 의 의미를 다르게 쓰면 unreviewed_count 가 "재확인해야 할 행 수"로
    읽히지 않는다.
    """
    _job_id, pair_id = _seed_job_and_pair(db_conn)

    CurationRepository().update_pair(pair_id, {"canonical_label": "정식명"})

    with db_conn.begin() as conn:
        stamp = conn.execute(
            text("SELECT reviewed_at FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()
    assert stamp is None


def test_release_gate_clears_flag_but_keeps_first_review_stamp(db_conn):
    job_id, _pair_id = _seed_job_and_pair(db_conn, reviewed=1)
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET curation_reviewed_at = '2026-01-01 09:00:00' WHERE id = :id"),
            {"id": job_id},
        )

    CurationRepository().release_gate(job_id)

    with db_conn.begin() as conn:
        row = (
            conn.execute(
                text("SELECT curation_reviewed, curation_reviewed_at FROM ocr_jobs WHERE id = :id"),
                {"id": job_id},
            )
            .mappings()
            .first()
        )
    assert row["curation_reviewed"] == 0
    assert str(row["curation_reviewed_at"]) == "2026-01-01 09:00:00"


def test_release_gate_does_not_stamp_curation_reviewed_at_when_never_reviewed(db_conn):
    """한 번도 검수되지 않은 잡(curation_reviewed_at IS NULL)에 release_gate를 호출해도
    curation_reviewed_at은 NULL로 남는다.

    release_gate는 `curation_reviewed = 0`만 갱신해야 한다 — `curation_reviewed_at =
    CURRENT_TIMESTAMP`가 끼어들면 "미검수"(한 번도 검수 안 한 잡)가 "재검수 필요"(과거에
    검수된 잡)로 오분류된다(§ release_gate docstring).
    """
    job_id, _pair_id = _seed_job_and_pair(db_conn, reviewed=0)

    CurationRepository().release_gate(job_id)

    with db_conn.begin() as conn:
        stamp = conn.execute(
            text("SELECT curation_reviewed_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert stamp is None


def test_mark_reviewed_stamps_first_review_time(db_conn):
    """한 번도 검수되지 않은 잡은 mark_reviewed가 curation_reviewed_at을 채운다."""
    job_id, _pair_id = _seed_job_and_pair(db_conn, reviewed=0)

    CurationRepository().mark_reviewed(job_id)

    with db_conn.begin() as conn:
        stamp = conn.execute(
            text("SELECT curation_reviewed_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert stamp is not None


def test_mark_reviewed_preserves_first_review_stamp_on_recheck(db_conn):
    """해제 → 재확정 왕복 후에도 curation_reviewed_at 은 첫 시각 그대로(COALESCE).

    첫 값을 과거 sentinel로 **직접 심는다**. mark_reviewed를 두 번 부르는 방식은
    curation_reviewed_at(DATETIME)·reviewed_at(TIMESTAMP)이 모두 소수초 0자리라
    같은 초 안에서는 COALESCE를 빼고 `= CURRENT_TIMESTAMP`로 구현해도 통과한다
    (false-green). 시간 경과에 의존하지 않게 고정한다.
    """
    job_id, _pair_id = _seed_job_and_pair(db_conn, reviewed=0)
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET curation_reviewed_at = '2020-01-01 00:00:00' WHERE id = :id"),
            {"id": job_id},
        )
    repo = CurationRepository()

    repo.mark_reviewed(job_id)
    repo.release_gate(job_id)
    repo.mark_reviewed(job_id)

    with db_conn.begin() as conn:
        stamp = conn.execute(
            text("SELECT curation_reviewed_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert str(stamp) == "2020-01-01 00:00:00"


def test_queries_expose_curation_reviewed_at(db_conn):
    job_id, _pair_id = _seed_job_and_pair(db_conn, reviewed=1)
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET curation_reviewed_at = '2026-01-01 09:00:00' WHERE id = :id"),
            {"id": job_id},
        )
    repo = CurationRepository()

    rows, _total = repo.list_jobs(20, 0)
    detail = repo.find_job_detail(job_id)

    summary = next(r for r in rows if r["job_id"] == job_id)
    assert str(summary["curation_reviewed_at"]) == "2026-01-01 09:00:00"
    assert str(detail["job"]["curation_reviewed_at"]) == "2026-01-01 09:00:00"
