"""db/migration_011_curation_reviewed_at.sql — 파일 자체를 읽어 실행해 백필 조건·멱등을 고정한다.

SQL을 테스트에 복사하지 않는다(test_migration_010_item_vocabulary와 같은 관용구) —
복사본은 파일과 갈린다. 이 마이그레이션의 핵심 위험은 백필 대상을 curation_reviewed = 1로
좁히는 회귀다(spec §4.1) — 그러면 ml/tools/blank_crop_report.py의 기계 경로가 이미
게이트를 해제해 둔 잡이 새 UI에서 영원히 "미검수"로 보인다.

다른 케이스는 schema_test.sql이 컬럼을 미리 만들어 두므로 가드의 'DO 0' 분기만 탄다 —
실제 ALTER 경로는 test_add_column_guard_actually_creates_the_column 하나가 담당한다.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration import _migration_sql

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_011_curation_reviewed_at.sql"

pytestmark = pytest.mark.usefixtures("db_conn")


def _apply(engine) -> None:
    _migration_sql.apply(engine, _MIGRATION)


def _seed_job(engine, *, reviewed: int) -> int:
    return _migration_sql.seed_job(engine, reviewed=reviewed)


def _seed_pair(engine, job_id: int, row_index: int, reviewed_at: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status, reviewed_at) "
                "VALUES (:r, :j, :i, '품목', '품목', 'included', :t)"
            ),
            {"r": f"job-{job_id}/row-{row_index}", "j": job_id, "i": row_index, "t": reviewed_at},
        )


def _stamp(engine, job_id: int):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT curation_reviewed_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()


def test_migration_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_backfills_reviewed_job_with_earliest_pair_stamp(db_conn):
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "2026-02-02 10:00:00")
    _seed_pair(db_conn, job_id, 1, "2026-01-01 09:00:00")  # 더 이른 시각

    _apply(db_conn)

    assert str(_stamp(db_conn, job_id)) == "2026-01-01 09:00:00"


def test_backfills_machine_released_job_too(db_conn):
    """기계 경로(blank_crop_report --recheck-reviewed)가 이미 해제한 잡도 회수한다.

    백필 대상을 curation_reviewed = 1로 좁히면 이 잡이 새 UI에서 "미검수"로 보인다 —
    실제로는 "재검수 필요"다(spec §4.1).
    """
    job_id = _seed_job(db_conn, reviewed=0)  # 게이트는 이미 해제됨
    _seed_pair(db_conn, job_id, 0, None)  # 기계가 수정해 NULL로 되돌린 쌍
    _seed_pair(db_conn, job_id, 1, "2026-03-03 08:00:00")  # 남아 있는 스탬프

    _apply(db_conn)

    assert str(_stamp(db_conn, job_id)) == "2026-03-03 08:00:00"


def test_leaves_never_reviewed_job_null(db_conn):
    job_id = _seed_job(db_conn, reviewed=0)
    _seed_pair(db_conn, job_id, 0, None)

    _apply(db_conn)

    assert _stamp(db_conn, job_id) is None


def test_is_idempotent_and_does_not_overwrite_existing_stamp(db_conn):
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "2026-01-01 09:00:00")

    _apply(db_conn)
    first = _stamp(db_conn, job_id)
    # 1차가 실제로 채웠는지 먼저 고정한다 — 이게 없으면 백필 UPDATE가 통째로 사라져도
    # first/second가 나란히 None이 되어 아래 단언이 공허하게 초록이다(실측 2026-08-05).
    assert str(first) == "2026-01-01 09:00:00"
    # 2차 실행 전에 쌍의 스탬프를 바꿔 둔다 — 백필이 덮으면 값이 따라 움직인다.
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE training_pairs SET reviewed_at = '2026-09-09 09:09:09' WHERE job_id = :j"),
            {"j": job_id},
        )
    _apply(db_conn)

    assert _stamp(db_conn, job_id) == first


def test_backfill_preserves_updated_at(db_conn):
    """백필 UPDATE가 ocr_jobs.updated_at의 ON UPDATE CURRENT_TIMESTAMP를 발동시키면 안 된다.

    migration_007이 updated_at을 ON UPDATE CURRENT_TIMESTAMP로 정의한다 — 값을 실제로
    바꾸는 UPDATE는 이 컬럼도 함께 현재 시각으로 리셋한다. 백필은 과거 잡의
    curation_reviewed_at만 채워야 하며 updated_at은 그대로 보존해야 한다(API가 이
    컬럼을 노출한다 — ocr_repository.py).
    """
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "2026-01-01 09:00:00")
    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = '2020-01-01 00:00:00' WHERE id = :id"),
            {"id": job_id},
        )

    _apply(db_conn)

    with db_conn.begin() as conn:
        updated_at = conn.execute(
            text("SELECT updated_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert str(updated_at) == "2020-01-01 00:00:00"


def test_add_column_guard_actually_creates_the_column(db_conn):
    """가드의 ALTER 분기를 실제로 태운다 — 기존 행 백필까지 한 번에.

    다른 케이스는 conftest 세션 fixture가 schema_test.sql로 컬럼을 미리 만들어 두므로
    @col_exists = 1 → 'DO 0' 분기만 지나간다. 즉 DDL 문법·타입·nullable이 한 번도
    검증되지 않는다 — 여기서만 컬럼을 지우고 진짜 ALTER를 태운다.

    운영 첫 실행의 모양은 "컬럼을 만들고 **그 자리에서** 기존 행을 백필한다"이므로 잡을
    미리 심어 둔다. 빈 테이블로 두면 그 조합이 어디서도 검증되지 않는다 — 실측
    (2026-08-05): 백필 UPDATE를 통째로 지워도 이 테스트는 초록이었다.

    DDL은 롤백되지 않는다. _apply가 중간에 실패하면 세션의 나머지 테스트가 전부
    깨지므로 finally에서 반드시 복구한다.
    """
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "2026-01-01 09:00:00")
    with db_conn.begin() as conn:
        conn.execute(text("ALTER TABLE ocr_jobs DROP COLUMN curation_reviewed_at"))
    try:
        _apply(db_conn)
    finally:
        with db_conn.begin() as conn:
            exists = conn.execute(
                text(
                    "SELECT COUNT(1) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'ocr_jobs' "
                    "AND column_name = 'curation_reviewed_at'"
                )
            ).scalar()
            if not exists:  # _apply가 실패한 경우의 안전망
                conn.execute(
                    text(
                        "ALTER TABLE ocr_jobs ADD COLUMN curation_reviewed_at DATETIME NULL "
                        "AFTER curation_reviewed"
                    )
                )

    with db_conn.begin() as conn:
        col = (
            conn.execute(
                text(
                    "SELECT DATA_TYPE, IS_NULLABLE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'ocr_jobs' "
                    "AND column_name = 'curation_reviewed_at'"
                )
            )
            .mappings()
            .first()
        )
    assert col is not None
    assert col["DATA_TYPE"] == "datetime"
    assert col["IS_NULLABLE"] == "YES"
    # 새로 만든 컬럼에 기존 행의 스탬프가 실제로 채워졌는지 — ALTER와 백필의 결합.
    assert str(_stamp(db_conn, job_id)) == "2026-01-01 09:00:00"
