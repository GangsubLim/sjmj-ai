"""db/migration_014_ocr_jobs_reprocess_seq.sql — 파일 자체를 읽어 실행해 생성·멱등을 고정한다.

SQL을 테스트에 복사하지 않는다(test_migration_012_training_pairs_draft_supply와 같은 관용구).
각 테스트는 컬럼을 떨어뜨려 자기 전제를 세우고 마이그레이션 적용으로 착지한다 —
세션 스키마(fixtures/schema_test.sql이 이미 만든다)에 대해 실행 순서 의존이 없다.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration import _migration_sql

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_014_ocr_jobs_reprocess_seq.sql"

_DROP = "ALTER TABLE ocr_jobs DROP COLUMN reprocess_seq"
_ADD = "ALTER TABLE ocr_jobs ADD COLUMN reprocess_seq INT NOT NULL DEFAULT 0"

pytestmark = pytest.mark.usefixtures("db_conn")


def _column(engine):
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'ocr_jobs' "
                    "AND column_name = 'reprocess_seq'"
                )
            )
            .mappings()
            .first()
        )


def _alter_count(engine) -> int:
    with engine.begin() as conn:
        return int(conn.execute(text("SHOW SESSION STATUS LIKE 'Com_alter_table'")).first()[1])


@pytest.fixture
def without_column(db_conn):
    """컬럼을 떨어뜨려 도입 전 전제를 세우고, 끝나면 반드시 되돌린다(DDL은 롤백되지 않는다)."""
    with db_conn.begin() as conn:
        conn.execute(text(_DROP))
    try:
        yield db_conn
    finally:
        try:
            if _MIGRATION.is_file():
                _migration_sql.apply(db_conn, _MIGRATION)
        finally:
            if _column(db_conn) is None:  # 파일이 아직 없거나 깨진 RED 구간의 안전망
                with db_conn.begin() as conn:
                    conn.execute(text(_ADD))


def test_migration_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_adds_a_not_null_zero_default_counter(without_column):
    assert _column(without_column) is None  # 전제 확인 — 도입 전 상태

    _migration_sql.apply(without_column, _MIGRATION)

    col = _column(without_column)
    assert col is not None
    assert col["DATA_TYPE"] == "int"
    assert col["IS_NULLABLE"] == "NO"
    assert int(col["COLUMN_DEFAULT"]) == 0


def test_existing_rows_land_on_generation_zero(without_column):
    """백필이 없는 것이 의도다 — 과거 잡의 논리 세대는 0이고 기하 파일도 없다(ADR 0012)."""
    job_id = _migration_sql.seed_job(without_column, reviewed=0)

    _migration_sql.apply(without_column, _MIGRATION)

    with without_column.begin() as conn:
        seq = conn.execute(
            text("SELECT reprocess_seq FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert seq == 0


def test_reapplying_the_migration_skips_the_alter(without_column):
    """두 번 먹여도 결과가 같고, 2회차는 ALTER를 실행하지 않아야 한다(원장 소실 복구 경로)."""
    before = _alter_count(without_column)
    _migration_sql.apply(without_column, _MIGRATION)
    after_first = _alter_count(without_column)
    _migration_sql.apply(without_column, _MIGRATION)
    after_second = _alter_count(without_column)

    assert after_first - before == 1
    assert after_second - after_first == 0


def test_migration_body_is_splittable_by_the_shared_runner_helper():
    """헬퍼 스플리터가 문장을 실제로 잘라내는지 — 주석만 남으면 apply가 조용히 no-op이다."""
    stmts = _migration_sql.statements(_MIGRATION.read_text(encoding="utf-8"))
    assert len(stmts) >= 5  # SET 2 + PREPARE + EXECUTE + DEALLOCATE
