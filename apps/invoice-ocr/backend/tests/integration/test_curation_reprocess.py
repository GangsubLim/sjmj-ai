"""재처리 전이의 repository 계약 — 실 MySQL."""

import json

import pytest
from sqlalchemy import event, text

from app.repositories.curation_repository import CurationRepository
from tests.fixtures.curation_helpers import rewind_job_token

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed(engine, status="done", result_json='{"rows": []}'):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, result_json) VALUES (:s, '/x.jpg', :r)"
            ),
            {"s": status, "r": result_json},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def test_find_job_for_update_reads_current_status(db_conn):
    job_id = _seed(db_conn, "done")

    assert CurationRepository().find_job_for_update(job_id) == {"id": job_id, "status": "done"}


def test_find_job_for_update_returns_none_for_unknown_job():
    assert CurationRepository().find_job_for_update(999999) is None


def test_requeue_for_reprocess_keeps_result_json(db_conn):
    """초안은 재처리 판별의 근거이자 실패 시 롤백 대상이다 — 한 바이트도 바뀌지 않는다.

    `is not None`으로는 '{}'로 덮어써도 통과한다. 워커의 재처리 판별자(#91)는 rows의
    존재를 보므로 빈 dict로 덮이면 그 잡이 신규로 재분류돼 확정 라벨 승계가 통째로
    사라진다 — 값 동일성까지 고정해야 그 회귀가 RED가 된다.
    """
    draft = '{"rows": [{"row_index": 0, "supply": 3000}], "supply_sum": 3000}'
    job_id = _seed(db_conn, "done", draft)

    CurationRepository().requeue_for_reprocess(job_id)

    with db_conn.begin() as conn:
        row = (
            conn.execute(
                text("SELECT status, result_json FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            )
            .mappings()
            .first()
        )
    assert row["status"] == "pending"
    assert json.loads(row["result_json"]) == json.loads(draft)


def test_requeue_for_reprocess_bumps_the_job_token(db_conn):
    """재처리 전이는 세대 토큰을 반드시 올린다 — 낙관적 잠금의 유일한 실측 자리(spec §12).

    토큰은 ocr_jobs.updated_at의 ON UPDATE CURRENT_TIMESTAMP에 얹혀 있어 새 컬럼이
    없다(마이그레이션 0). 그 얹힘이 깨지면(updated_at을 명시 대입하는 UPDATE로 바뀌는 등)
    재처리 직후에도 옛 화면의 토큰이 유효해져 PATCH·검수 완료가 409 없이 통과한다.

    같은 초 안의 전이는 값이 안 변할 수 있다 — 전이 전 updated_at을 1초 과거로 밀어
    시간·해상도에 기대지 않고 벌린다(밀리초 전환 #95 이후에도 같은 방식이 성립한다).
    """
    job_id = _seed(db_conn, "done")
    rewind_job_token(db_conn, job_id)
    repo = CurationRepository()
    before = repo.get_job_token(job_id)

    repo.requeue_for_reprocess(job_id)

    assert repo.get_job_token(job_id) != before


def test_requeue_bumps_the_reprocess_generation_in_the_same_update(db_conn):
    """세대 증가 지점은 하나다 — status 전이와 같은 UPDATE에서 오른다(spec §6-2).

    두 문장으로 나누면 그 사이에 워커가 잡을 집어 옛 세대로 기하를 스탬프할 창이 생긴다.
    최종 상태만 보면 두 UPDATE로 쪼개져도 같은 값에 도달하므로, 실행된 문장 수 자체를
    센다 — SQLAlchemy 이벤트로 커서에 넘어간 SQL을 가로챈다.
    """
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/a.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()

    statements = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "ocr_jobs" in statement and "UPDATE" in statement.upper():
            statements.append(statement)

    event.listen(db_conn, "before_cursor_execute", _capture)
    try:
        CurationRepository().requeue_for_reprocess(job_id)
    finally:
        event.remove(db_conn, "before_cursor_execute", _capture)

    assert len(statements) == 1
    assert "reprocess_seq" in statements[0]
    assert "status" in statements[0]

    with db_conn.begin() as conn:
        row = (
            conn.execute(
                text("SELECT status, reprocess_seq FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            )
            .mappings()
            .first()
        )
    assert row["status"] == "pending"
    assert row["reprocess_seq"] == 1


def test_requeue_twice_reaches_generation_two(db_conn):
    """멱등이 아니다 — 재처리 요청마다 논리 세대가 하나씩 오른다."""
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/b.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()

    repo = CurationRepository()
    repo.requeue_for_reprocess(job_id)
    with db_conn.begin() as conn:
        conn.execute(text("UPDATE ocr_jobs SET status='done' WHERE id = :id"), {"id": job_id})
    repo.requeue_for_reprocess(job_id)

    with db_conn.begin() as conn:
        seq = conn.execute(
            text("SELECT reprocess_seq FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert seq == 2
