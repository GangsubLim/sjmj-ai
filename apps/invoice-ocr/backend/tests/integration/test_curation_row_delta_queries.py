"""list_jobs의 행 증감 투영·필터 SQL 검증 — 실 MySQL(sjmj_test) 대상.

값 없음(correction 부재·키 부재·JSON 타입 불일치)이 0으로 오염되지 않는지, 필터가
목록과 total을 같은 조건으로 좁히는지, 필터 off가 기존 목록과 완전히 같은지를 고정한다.
"""

import json

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job_with_pair(engine, *, created_at="2026-09-01 09:00:00"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, created_at) "
                "VALUES ('done', '/x.jpg', :c)"
            ),
            {"c": created_at},
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                "VALUES (:r, :j, 0, '품목', '품목', 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
    return job_id


def _seed_correction(engine, job_id, correction):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_corrections (job_id, correction_json) VALUES (:j, :c)"),
            {"j": job_id, "c": json.dumps(correction, ensure_ascii=False)},
        )


def _row(rows, job_id):
    return next(r for r in rows if r["job_id"] == job_id)


def test_list_jobs_projects_row_delta_from_correction_json(db_conn):
    job_id = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, job_id, {"lines": [], "rows_added": 2, "rows_dropped": 1})

    rows, _total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["rows_added"] == 2
    assert row["rows_dropped"] == 1


def test_row_delta_is_null_when_job_has_no_correction(db_conn):
    job_id = _seed_job_with_pair(db_conn)

    rows, _total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["rows_added"] is None
    assert row["rows_dropped"] is None


def test_row_delta_is_null_when_keys_missing(db_conn):
    # 구 데이터 — lines만 있고 두 수가 없다. 0으로 닫으면 "증감 없음"으로 위장된다.
    job_id = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, job_id, {"lines": []})

    rows, _total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["rows_added"] is None
    assert row["rows_dropped"] is None


def test_row_delta_is_null_when_json_type_mismatches(db_conn):
    # 정수가 아닌 값은 신뢰하지 않는다(ocr_observation의 rows_type != 'ARRAY'와 같은 규율).
    job_id = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, job_id, {"lines": [], "rows_added": "2", "rows_dropped": None})

    rows, _total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["rows_added"] is None
    assert row["rows_dropped"] is None


def test_row_delta_projects_each_key_independently(db_conn):
    # 두 필드를 하나로 접는 구현(한쪽이 무효면 둘 다 null)을 반증한다.
    job_id = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, job_id, {"lines": [], "rows_added": 2})

    rows, _total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["rows_added"] == 2
    assert row["rows_dropped"] is None


def test_row_delta_filter_uses_three_valued_logic_per_field(db_conn):
    # (NULL > 0 OR 2 > 0) = TRUE → 남는다.
    partial_hit = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, partial_hit, {"lines": [], "rows_dropped": 2})
    # (0 > 0 OR NULL > 0) = NULL → 빠진다(값 없음은 대상 아님).
    partial_miss = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, partial_miss, {"lines": [], "rows_added": 0})

    rows, total = CurationRepository().list_jobs(20, 0, row_delta=True)

    assert {r["job_id"] for r in rows} == {partial_hit}
    assert total == 1


def test_row_delta_filter_narrows_rows_and_total(db_conn):
    added = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, added, {"lines": [], "rows_added": 1, "rows_dropped": 0})
    dropped = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, dropped, {"lines": [], "rows_added": 0, "rows_dropped": 3})
    flat = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, flat, {"lines": [], "rows_added": 0, "rows_dropped": 0})
    absent = _seed_job_with_pair(db_conn)

    rows, total = CurationRepository().list_jobs(20, 0, row_delta=True)

    ids = {r["job_id"] for r in rows}
    assert ids == {added, dropped}
    # total이 목록과 다른 조건으로 세면 필터 켠 화면의 총건수가 "남은 일"이 아니게 된다.
    assert total == 2
    assert flat not in ids and absent not in ids


def test_filter_off_returns_all_jobs_and_untouched_total(db_conn):
    added = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, added, {"lines": [], "rows_added": 1, "rows_dropped": 0})
    flat = _seed_job_with_pair(db_conn)

    rows, total = CurationRepository().list_jobs(20, 0)

    ids = {r["job_id"] for r in rows}
    assert ids == {added, flat}
    assert total == 2


def test_filter_off_preserves_existing_order(db_conn):
    # 정렬 계약 회귀 방지 — 미검수 우선, 그다음 생성 최신순.
    old = _seed_job_with_pair(db_conn, created_at="2026-09-01 09:00:00")
    new = _seed_job_with_pair(db_conn, created_at="2026-09-02 09:00:00")
    with db_conn.begin() as conn:
        conn.execute(text("UPDATE ocr_jobs SET curation_reviewed = 1 WHERE id = :id"), {"id": new})

    rows, _total = CurationRepository().list_jobs(20, 0)

    ids = [r["job_id"] for r in rows]
    assert ids.index(old) < ids.index(new)


def test_filter_off_orders_newest_first_within_same_review_state(db_conn):
    # 2차 키(created_at DESC) — 검수상태가 같으면 최신 잡이 앞이다.
    old = _seed_job_with_pair(db_conn, created_at="2026-09-01 09:00:00")
    new = _seed_job_with_pair(db_conn, created_at="2026-09-02 09:00:00")

    rows, _total = CurationRepository().list_jobs(20, 0)

    ids = [r["job_id"] for r in rows]
    assert ids.index(new) < ids.index(old)


def test_filter_off_breaks_created_at_ties_by_id_desc(db_conn):
    # 3차 키(id DESC) — 같은 초에 생성된 잡의 순서가 흔들리면 페이지 경계가 요동친다.
    first = _seed_job_with_pair(db_conn, created_at="2026-09-01 09:00:00")
    second = _seed_job_with_pair(db_conn, created_at="2026-09-01 09:00:00")

    rows, _total = CurationRepository().list_jobs(20, 0)

    ids = [r["job_id"] for r in rows]
    assert ids.index(second) < ids.index(first)


def test_multiple_corrections_do_not_inflate_pair_count(db_conn):
    # crop_ref UNIQUE + 단일 트랜잭션이라 재확정 대부분은 롤백되지만, 쌍 0건 잡이나 유지
    # 집합이 서로소인 재확정에서는 job_id가 1:N으로 남는다. 소박한 LEFT JOIN이면 그때
    # COUNT(tp.id)가 배로 부푼다.
    job_id = _seed_job_with_pair(db_conn)
    _seed_correction(db_conn, job_id, {"lines": [], "rows_added": 9, "rows_dropped": 9})
    _seed_correction(db_conn, job_id, {"lines": [], "rows_added": 1, "rows_dropped": 0})

    rows, total = CurationRepository().list_jobs(20, 0)

    row = _row(rows, job_id)
    assert row["pair_count"] == 1
    assert total == 1
    # 최신 correction이 그 잡의 관측치다.
    assert row["rows_added"] == 1
    assert row["rows_dropped"] == 0
