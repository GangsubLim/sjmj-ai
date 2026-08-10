"""db/migration_012_training_pairs_draft_supply.sql — 파일 자체를 읽어 실행해 백필을 고정한다.

SQL을 테스트에 복사하지 않는다(test_migration_010·011과 같은 관용구) — 복사본은 파일과 갈린다.
이 파일이 없으면 conftest가 fixtures/schema_test.sql로 컬럼을 미리 만들어 두므로 가드의
@col_exists = 1 → 'DO 0' 분기만 지나가고, ALTER 문법·JSON_TABLE·collation·백필 UPDATE가
CI에서 한 번도 실행되지 않는다(migration_011의 실측 2026-08-05: 백필 UPDATE를 통째로 지워도
스위트가 초록이었다).
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration import _migration_sql

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_012_training_pairs_draft_supply.sql"

pytestmark = pytest.mark.usefixtures("db_conn")


def _apply(engine) -> None:
    _migration_sql.apply(engine, _MIGRATION)


def _line(crop_ref: str, *, final_label, final_supply, draft_supply) -> dict:
    """확정 시점 correction_json의 lines[] 1개 — build_correction의 실제 shape."""
    return {
        "crop_ref": crop_ref,
        "draft_label": None,
        "final_label": final_label,
        "label_changed": True,
        "draft_supply": draft_supply,
        "final_supply": final_supply,
        "supply_changed": True,
        "label_source": None,
    }


def _seed_correction(engine, job_id: int, lines: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ocr_corrections (job_id, correction_json) VALUES (:j, :c)"),
            {
                "j": job_id,
                "c": json.dumps(
                    {"lines": lines, "rows_added": 0, "rows_dropped": 0}, ensure_ascii=False
                ),
            },
        )


def _seed_pair(
    engine,
    job_id: int,
    *,
    crop_ref: str,
    row_index: int,
    final_label,
    supply,
    draft_supply=None,
) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, draft_supply, final_label, "
                "canonical_label, supply, status) "
                "VALUES (:r, :j, :i, NULL, :d, :f, :f, :s, 'included')"
            ),
            {
                "r": crop_ref,
                "j": job_id,
                "i": row_index,
                "d": draft_supply,
                "f": final_label,
                "s": supply,
            },
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _draft(engine, pair_id: int):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT draft_supply FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()


def test_migration_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_backfills_a_row_shaped_pair(db_conn):
    """오늘 ② 앵커가 정상 동작 중인 row- 형식 쌍도 백필 대상이다(spec §2.1 F1).

    미결 27건만 백필하면 나머지 70건이 NULL로 남아 다음 재처리에서 ②가 통째로 죽는다 —
    미결이 지금보다 늘어나는 순회귀다.
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0", final_label="목살", final_supply=720000, draft_supply=1720000
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) == 1720000


def test_backfills_an_orphaned_pair_through_the_reverse_key(db_conn):
    """이 이슈의 본체 — 미결(orphan-) 쌍도 역산키로 확정 시점 초안을 회수한다.

    미결 전환(worker/db.commit_job ①)은 crop_ref만 orphan-으로 옮기고 row_index는 그대로
    두므로, 확정 시점 좌표를 job-{job_id}/row-{row_index}로 역산할 수 있다(운영 실측 27/27).
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0", final_label="목살", final_supply=720000, draft_supply=1720000
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/orphan-77",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) == 1720000


def test_does_not_backfill_when_the_final_label_disagrees(db_conn):
    """역산키 적중 ≠ 정확(spec §2.1 F2). 승계로 row_index가 옮겨간 쌍은 그 자리의 확정
    시점 line이 다른 행이다 — 정합 가드가 없으면 남의 초안 금액을 앵커로 받는다.
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0",
                final_label="삼겹살",
                final_supply=720000,
                draft_supply=1720000,
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) is None


def test_does_not_backfill_when_the_supply_disagrees(db_conn):
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0", final_label="목살", final_supply=500000, draft_supply=1720000
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) is None


def test_leaves_null_when_the_source_draft_is_null(db_conn):
    """모델이 금액을 못 읽은 행 — 앵커 없음이 정상 표현이다(spec §3)."""
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [_line(f"job-{job_id}/row-0", final_label="목살", final_supply=720000, draft_supply=None)],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) is None


def test_does_not_backfill_an_out_of_range_draft(db_conn):
    """초안 금액에는 상한이 없다(spec §2.3) — 범위 밖은 백필하지 않는다(결정 6).

    parse_amount는 원문의 모든 숫자 run을 길이 제한 없이 이어붙이므로 INT는 물론 BIGINT도
    넘길 수 있다. 가드가 없으면 마이그레이션 자체가 1264로 죽는다.
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0",
                final_label="목살",
                final_supply=720000,
                draft_supply=2147483648,
            ),
            _line(f"job-{job_id}/row-1", final_label="갈비", final_supply=300000, draft_supply=-1),
        ],
    )
    over = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )
    under = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-1",
        row_index=1,
        final_label="갈비",
        supply=300000,
    )

    _apply(db_conn)

    assert _draft(db_conn, over) is None
    assert _draft(db_conn, under) is None


def test_does_not_backfill_a_non_integer_draft(db_conn):
    """비정수 JSON 초안은 백엔드 _anchorable_supply와 같게 격리한다.

    DECIMAL PATH 추출은 JSON 문자열·실수·bool을 조용히 정수로 강제 변환한다
    (실 MySQL 실측: "120000"→120000, 1.5→2, true→1). 백엔드는 셋 다 NULL로 격리하므로
    타입 가드가 없으면 같은 컬럼의 의미론이 적재 경로별로 갈린다. 게다가 멱등 조건
    WHERE draft_supply IS NULL 때문에 한 번 들어간 거짓 앵커는 재실행으로도 교정되지 않는다.
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0",
                final_label="목살",
                final_supply=720000,
                draft_supply="120000",
            ),
            _line(f"job-{job_id}/row-1", final_label="갈비", final_supply=300000, draft_supply=1.5),
            _line(
                f"job-{job_id}/row-2", final_label="등심", final_supply=100000, draft_supply=True
            ),
        ],
    )
    as_text = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )
    as_float = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-1",
        row_index=1,
        final_label="갈비",
        supply=300000,
    )
    as_bool = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-2",
        row_index=2,
        final_label="등심",
        supply=100000,
    )

    _apply(db_conn)

    assert _draft(db_conn, as_text) is None
    assert _draft(db_conn, as_float) is None
    assert _draft(db_conn, as_bool) is None


def test_joins_a_korean_label_without_a_collation_error(db_conn):
    """ERROR 1267 회귀 가드.

    JSON_TABLE 출력은 utf8mb4_0900_ai_ci이고 training_pairs는 migration_008이 명시한
    utf8mb4_unicode_ci다 — CONVERT + COLLATE 없이 비교하면
    'Illegal mix of collations'로 마이그레이션이 죽는다(spec 검증 중 실제 발생).
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0",
                final_label="돼지고기(냉장)",
                final_supply=720000,
                draft_supply=1720000,
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="돼지고기(냉장)",
        supply=720000,
    )

    _apply(db_conn)

    assert _draft(db_conn, pair_id) == 1720000


def test_is_idempotent_and_does_not_overwrite_an_existing_value(db_conn):
    """WHERE draft_supply IS NULL이 원장 밖 재실행에서도 앱이 쓴 값을 덮지 않게 한다.

    배포 후 창 B 재백필(spec §2.4)이 같은 UPDATE를 1회 더 돌리는 것이 이 멱등성 위에 선다.
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0", final_label="목살", final_supply=720000, draft_supply=1720000
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )

    _apply(db_conn)
    # 1차가 실제로 채웠는지 먼저 고정한다 — 없으면 백필 UPDATE가 통째로 사라져도
    # 아래 단언이 None == None으로 공허하게 초록이다(migration_011의 실측 교훈).
    assert _draft(db_conn, pair_id) == 1720000

    with db_conn.begin() as conn:
        conn.execute(
            text("UPDATE ocr_corrections SET correction_json = :c WHERE job_id = :j"),
            {
                "c": json.dumps(
                    {
                        "lines": [
                            _line(
                                f"job-{job_id}/row-0",
                                final_label="목살",
                                final_supply=720000,
                                draft_supply=999000,
                            )
                        ],
                        "rows_added": 0,
                        "rows_dropped": 0,
                    },
                    ensure_ascii=False,
                ),
                "j": job_id,
            },
        )
    _apply(db_conn)

    assert _draft(db_conn, pair_id) == 1720000


def test_add_column_guard_actually_creates_the_column(db_conn):
    """가드의 ALTER 분기를 실제로 태운다 — 기존 행 백필까지 한 번에.

    다른 케이스는 conftest 세션 fixture가 schema_test.sql로 컬럼을 미리 만들어 두므로
    @col_exists = 1 → 'DO 0' 분기만 지나간다. 즉 DDL 문법·타입·nullable이 한 번도
    검증되지 않는다 — 여기서만 컬럼을 지우고 진짜 ALTER를 태운다.

    DDL은 롤백되지 않는다. _apply가 중간에 실패하면 세션의 나머지 테스트가 전부
    깨지므로 finally에서 반드시 복구한다(migration_011과 같은 관용구).
    """
    job_id = _migration_sql.seed_job(db_conn, reviewed=0)
    _seed_correction(
        db_conn,
        job_id,
        [
            _line(
                f"job-{job_id}/row-0", final_label="목살", final_supply=720000, draft_supply=1720000
            )
        ],
    )
    pair_id = _seed_pair(
        db_conn,
        job_id,
        crop_ref=f"job-{job_id}/row-0",
        row_index=0,
        final_label="목살",
        supply=720000,
    )
    with db_conn.begin() as conn:
        conn.execute(text("ALTER TABLE training_pairs DROP COLUMN draft_supply"))
    try:
        _apply(db_conn)
    finally:
        with db_conn.begin() as conn:
            exists = conn.execute(
                text(
                    "SELECT COUNT(1) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'training_pairs' "
                    "AND column_name = 'draft_supply'"
                )
            ).scalar()
            if not exists:  # _apply가 실패한 경우의 안전망
                conn.execute(
                    text(
                        "ALTER TABLE training_pairs ADD COLUMN draft_supply INT NULL "
                        "AFTER draft_label"
                    )
                )

    with db_conn.begin() as conn:
        col = (
            conn.execute(
                text(
                    "SELECT DATA_TYPE, IS_NULLABLE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'training_pairs' "
                    "AND column_name = 'draft_supply'"
                )
            )
            .mappings()
            .first()
        )
    assert col is not None
    assert (col["DATA_TYPE"], col["IS_NULLABLE"]) == ("int", "YES")
    # 새로 만든 컬럼에 기존 행의 초안이 실제로 채워졌는지 — ALTER와 백필의 결합.
    assert _draft(db_conn, pair_id) == 1720000
