"""db/migration_013_ocr_jobs_updated_at_ms.sql — 파일 자체를 읽어 실행해 승격·멱등을 고정한다.

SQL을 테스트에 복사하지 않는다(test_migration_011_curation_reviewed_at과 같은 관용구) —
복사본은 파일과 갈린다. 각 테스트는 정밀도를 0으로 되돌리는 것으로 자기 전제를 세우고
마이그레이션 적용으로 3에 착지한다 — 세션 스키마(fixtures/schema_test.sql이 이미 3으로
만든다)에 대해 실행 순서 의존이 없다.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository
from tests.integration import _migration_sql

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_013_ocr_jobs_updated_at_ms.sql"

_TO_SECOND_PRECISION = (
    "ALTER TABLE ocr_jobs MODIFY updated_at TIMESTAMP NOT NULL "
    "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
)

pytestmark = pytest.mark.usefixtures("db_conn")


def _precision(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "SELECT datetime_precision FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'ocr_jobs' "
                "AND column_name = 'updated_at'"
            )
        ).scalar()


_TO_MS_PRECISION = (
    "ALTER TABLE ocr_jobs MODIFY updated_at TIMESTAMP(3) NOT NULL "
    "DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
)


@pytest.fixture
def second_precision(db_conn):
    """updated_at 을 초 정밀도로 되돌려 승격 전 전제를 세우고, 끝나면 반드시 되돌린다.

    DDL 은 롤백되지 않고 conftest 의 세션 엔진은 스키마를 한 번만 만든다(_reset 은 TRUNCATE
    뿐) — 테스트가 중간에 실패하면 세션의 나머지가 초 정밀도 위에서 돈다. 형제
    test_migration_011…::test_add_column_guard_actually_creates_the_column 과 같은
    try/finally + 안전망 관용구다.

    아래 강등 SQL 은 마이그레이션 본문의 복사본이 아니라 **테스트 전용 전제 세팅**이다 —
    이 파일 docstring 의 "SQL 을 테스트에 복사하지 않는다"는 승격 본문에 대한 규칙이다.
    """
    with db_conn.begin() as conn:
        conn.execute(text(_TO_SECOND_PRECISION))
    try:
        yield db_conn
    finally:
        if _MIGRATION.is_file():
            _migration_sql.apply(db_conn, _MIGRATION)
        if _precision(db_conn) != 3:  # 파일이 아직 없거나 깨진 RED 구간의 안전망
            with db_conn.begin() as conn:
                conn.execute(text(_TO_MS_PRECISION))


def test_migration_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_upgrades_updated_at_to_millisecond_precision(second_precision):
    assert _precision(second_precision) == 0  # 전제 확인 — 승격 전 상태

    _migration_sql.apply(second_precision, _MIGRATION)

    assert _precision(second_precision) == 3


def _alter_count(engine) -> int:
    """서버 전역 ALTER TABLE 실행 횟수 — 가드가 DDL을 실제로 건너뛰는지 재는 계측 축."""
    with engine.begin() as conn:
        return int(conn.execute(text("SHOW GLOBAL STATUS LIKE 'Com_alter_table'")).first()[1])


def test_reapplying_the_migration_skips_the_alter(second_precision):
    """두 번 먹여도 결과가 같고, 2 회차는 ALTER 를 **실행하지 않아야** 한다.

    정밀도만 단언하면 가드를 통째로 지워도 통과한다 — 무가드 ALTER 를 두 번 돌려도 최종
    정밀도는 3 이다. MODIFY 는 테이블 재작성이라 무가드 재실행 비용이 크고, 원장
    (schema_migrations)이 사라지는 복구 경로가 실재한다(db/README.md).

    계측은 전역 카운터 Com_alter_table 이다(로컬 실측: 1 회차 +1, 가드 통과 2 회차 +0).
    다른 세션의 DDL 이 섞이면 델타가 커져 false RED 가 날 뿐 false GREEN 은 나오지 않는다 —
    스위트는 직렬 실행이고 오차 방향이 안전한 쪽이라 그대로 쓴다.
    """
    before = _alter_count(second_precision)
    _migration_sql.apply(second_precision, _MIGRATION)
    after_first = _alter_count(second_precision)
    _migration_sql.apply(second_precision, _MIGRATION)
    after_second = _alter_count(second_precision)

    assert after_first - before == 1
    assert after_second - after_first == 0
    assert _precision(second_precision) == 3


def test_migration_body_is_splittable_by_the_shared_runner_helper():
    """헬퍼 스플리터가 문장을 실제로 잘라내는지 — 주석만 남으면 apply가 조용히 no-op이다.

    가드를 삭제하면 문장이 5개에서 1개로 줄어 이 단언도 함께 RED가 된다 — 멱등 계측의 보조 축이다.
    """
    stmts = _migration_sql.statements(_MIGRATION.read_text(encoding="utf-8"))
    assert len(stmts) >= 5  # SET 2 + PREPARE + EXECUTE + DEALLOCATE


def test_existing_rows_keep_their_second_and_gain_a_zero_fraction(second_precision):
    """데이터가 있는 테이블에서의 MODIFY — 초 부분 보존 + .000 획득(운영 적용의 실제 모양).

    형제가 남긴 교훈과 같다(test_migration_011: 빈 테이블로 두면 그 조합이 어디서도
    검증되지 않는다 — 실측으로 백필을 통째로 지워도 초록이었다). 이 단언이 PR 본문의
    "배포 직후 열려 있던 화면의 옛 토큰이 1 회 409 를 받는다"는 서술의 유일한 실증 근거다 —
    값 자체는 보존되고 토큰 문자열의 모양만 바뀐다. epoch 리터럴은 쓰지 않는다(세션 tz 의존).
    """
    job_id = _migration_sql.seed_job(second_precision, reviewed=0)
    with second_precision.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = '2026-09-01 12:00:00' WHERE id = :id"),
            {"id": job_id},
        )
    before = CurationRepository().get_job_token(job_id)

    _migration_sql.apply(second_precision, _MIGRATION)

    assert CurationRepository().get_job_token(job_id) == f"{before}.000"
