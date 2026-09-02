"""세대 토큰의 해상도 행동을 실 MySQL로 고정한다(#95-1).

스키마 정밀도 단언(information_schema.datetime_precision = 3)만으로는 대체할 수 없다 —
토큰 SQL이 정수 초로 퇴행해도 정밀도 단언과 발급·대조 왕복 테스트는 그대로 통과한다.
updated_at을 같은 초의 서로 다른 밀리초로 **명시 설정**해 시계 의존 없이 결정론으로 잰다
(UPDATE가 updated_at을 명시 지정하면 ON UPDATE CURRENT_TIMESTAMP가 발동하지 않는다).
"""

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def _seed_job(engine) -> int:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/ms.jpg')"))
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _set_updated_at(engine, job_id: int, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = :v WHERE id = :id"),
            {"v": value, "id": job_id},
        )


def test_token_differs_between_two_millisecond_updates_in_the_same_second(db_conn):
    job_id = _seed_job(db_conn)
    repo = CurationRepository()

    _set_updated_at(db_conn, job_id, "2026-09-01 12:00:00.001")
    first = repo.get_job_token(job_id)
    # updated_at 불변이면 토큰도 불변 — 벽시계 결합 회귀 차단
    assert repo.get_job_token(job_id) == first
    _set_updated_at(db_conn, job_id, "2026-09-01 12:00:00.002")
    second = repo.get_job_token(job_id)

    assert first != second


def test_issued_token_also_carries_millisecond_resolution(db_conn):
    """발급 경로(find_job_detail)도 같은 해상도여야 한다 — 대조 경로만 올리면 반쪽이다."""
    job_id = _seed_job(db_conn)
    repo = CurationRepository()

    _set_updated_at(db_conn, job_id, "2026-09-01 12:00:00.001")
    first = repo.find_job_detail(job_id)["job"]["job_token"]
    _set_updated_at(db_conn, job_id, "2026-09-01 12:00:00.002")
    second = repo.find_job_detail(job_id)["job"]["job_token"]

    assert first != second
    # 발급·대조 왕복은 밀리초 스키마에서도 일치해야 한다.
    assert second == repo.get_job_token(job_id)
