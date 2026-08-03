"""테스트 스키마가 운영의 collation 분기를 재현하는지 고정한다(#40, spec §3.5).

운영은 training_pairs(utf8mb4_unicode_ci)와 item_suggestions(utf8mb4_0900_ai_ci)가 갈려 있다
(2026-07-31 운영 DB 실측). 갈린 경위는 미검증이다 — db/schema.sql·db/migration_002는
COLLATE 무명시이고 부트스트랩 지시(db/migration_poc_to_production.sql:7)는 DB 기본을
utf8mb4_unicode_ci로 만들므로 "MySQL 8 서버 기본값" 설명은 성립하지 않는다.
실측값을 정본으로 삼고 경위는 추정하지 않는다.
테스트 스키마가 둘을 같은 collation으로 만들면, 이 이슈가 다루는 바로 그 비교가
테스트에서는 통과하고 운영에서만 ERROR 1267로 깨진다.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.repositories.items_repository import ItemRepository

pytestmark = pytest.mark.usefixtures("db_conn")


def test_item_suggestions_string_columns_use_production_collation(db_conn):
    """테이블 레벨 COLLATE 변경은 문자열 컬럼 전부에 걸린다 — Produces 문구(`item_suggestions.*`)와 맞춘다."""
    with db_conn.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, collation_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'item_suggestions' "
                "AND collation_name IS NOT NULL"
            )
        ).all()
    assert {c for c, _ in rows} == {"item_name", "default_unit", "category", "notes"}
    assert {col for _, col in rows} == {"utf8mb4_0900_ai_ci"}


def test_training_pairs_canonical_label_keeps_unicode_ci(db_conn):
    """반대편은 건드리지 않는다 — 분기 자체가 재현 대상이다."""
    with db_conn.begin() as conn:
        collation = conn.execute(
            text(
                "SELECT collation_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'training_pairs' "
                "AND column_name = 'canonical_label'"
            )
        ).scalar()
    assert collation == "utf8mb4_unicode_ci"


def test_bare_join_across_the_two_tables_fails_with_1267(db_conn):
    """COLLATE 없는 비교는 운영과 같은 ERROR 1267로 죽어야 한다.

    이 단언이 곧 "테스트가 운영 조건을 재현한다"의 행동적 증명이다. 진단 SQL(런북)과
    마이그레이션이 COLLATE를 명시해야 하는 이유가 여기서 고정된다.
    """
    with pytest.raises(OperationalError) as excinfo, db_conn.begin() as conn:
        conn.execute(
            text(
                "SELECT 1 FROM training_pairs tp "
                "LEFT JOIN item_suggestions it ON it.item_name = tp.canonical_label"
            )
        )
    assert excinfo.value.orig.args[0] == 1267


def _seed_included_pair(engine, label):
    """검수완료 잡 + included 쌍 1건을 심고 job_id를 반환한다."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/c.jpg', 1)"
            )
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                "VALUES (:r, :j, 0, :l, :l, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id, "l": label},
        )
    return job_id


def test_registered_label_is_findable_across_the_collation_boundary(db_conn):
    """unicode_ci 테이블에서 읽은 라벨을 0900_ai_ci 테이블에 등록하고 명시 COLLATE로 다시 찾는다.

    경계를 실제로 넘는 것은 마지막 검증 SELECT의 명시 COLLATE 조인 하나뿐이다 —
    ensure_exists의 INSERT는 bind 파라미터라 값이 대상 컬럼 collation으로 강제되고,
    collation이 어떻든 ERROR 1267이 날 수 없다. 이 테스트가 고정하는 것은 "등록 경로가
    경계를 가로지른다"가 아니라 "등록된 라벨을 명시 COLLATE 조인으로 되찾을 수 있다"이며,
    그 조인이 진단 SQL·마이그레이션이 쓰는 것과 같은 형태다.
    """
    job_id = _seed_included_pair(db_conn, "중고")
    with db_conn.begin() as conn:
        label = conn.execute(
            text("SELECT canonical_label FROM training_pairs WHERE job_id = :j"), {"j": job_id}
        ).scalar()

    ItemRepository().ensure_exists(label)

    with db_conn.begin() as conn:
        found = conn.execute(
            text(
                "SELECT it.id FROM training_pairs tp "
                "JOIN item_suggestions it "
                "  ON it.item_name = tp.canonical_label COLLATE utf8mb4_0900_ai_ci "
                "WHERE tp.job_id = :j"
            ),
            {"j": job_id},
        ).scalar()
    assert found is not None
