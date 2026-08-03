"""테스트 스키마가 운영의 collation 분기를 재현하는지 고정한다(#40, spec §3.5).

운영은 training_pairs(utf8mb4_unicode_ci)와 item_suggestions(utf8mb4_0900_ai_ci)가 갈려 있다
(2026-07-31 운영 DB 실측). 갈린 경위는 미검증이다 — 부트스트랩 지시
(db/migration_poc_to_production.sql:7)는 DB 기본을 utf8mb4_unicode_ci로 만들고
db/schema.sql·db/migration_002도 원래 COLLATE 무명시였으므로 "MySQL 8 서버 기본값" 설명은
성립하지 않는다. 실측값을 정본으로 삼고 경위는 추정하지 않는다(#40에서 db/schema.sql의
item_suggestions에는 그 실측값이 명시로 반영됐다).
테스트 스키마가 둘을 같은 collation으로 만들면, 이 이슈가 다루는 바로 그 비교가
테스트에서는 통과하고 운영에서만 ERROR 1267로 깨진다.

전제: MySQL 8.0+ 서버. utf8mb4_0900_ai_ci는 8.0에서 도입됐고 collation 혼합 비교의
ERROR 1267도 그 위에서 난다(로컬 9.6 / CI mysql:8).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.repositories.items_repository import ItemRepository

pytestmark = pytest.mark.usefixtures("db_conn")

# ER_CANT_AGGREGATE_2COLLATIONS — collation이 갈린 두 문자열을 명시 COLLATE 없이 비교할 때.
ERR_CANT_AGGREGATE_2COLLATIONS = 1267


def test_item_suggestions_string_columns_use_production_collation(db_conn):
    """테이블 레벨 COLLATE 변경은 문자열 컬럼 전부에 걸린다 — Produces 문구(`item_suggestions.*`)와 맞춘다.

    컬럼 이름 집합을 정확 일치로 못박지 않는다 — collation과 무관한 스키마 진화(문자열 컬럼
    추가)로 이 테스트가 빨개지면 안 된다. 불변식은 "문자열 컬럼이 전부 목적지 collation"이고,
    등록·진단이 실제로 쓰는 item_name이 그 안에 있는지만 함께 확인한다.
    """
    with db_conn.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, collation_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'item_suggestions' "
                "AND collation_name IS NOT NULL"
            )
        ).all()
    column_names = {column_name for column_name, _ in rows}
    collations = {collation for _, collation in rows}
    assert "item_name" in column_names
    assert collations == {"utf8mb4_0900_ai_ci"}


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
    assert excinfo.value.orig.args[0] == ERR_CANT_AGGREGATE_2COLLATIONS


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


def test_explicit_collate_direction_follows_the_destination_unique_index(db_conn):
    """명시 COLLATE의 *방향*을 고정한다 — 반대 방향으로 되돌리면 이 테스트가 깨진다.

    위 테스트의 시드 라벨('중고')은 앞뒤 공백이 없어 방향을 고정하지 못한다 —
    utf8mb4_unicode_ci로 바꿔도 통과한다. 방향이 결과를 바꾸는 축은 PAD 규칙이다
    (로컬 MySQL 9.6 실측, information_schema.collations의 PAD_ATTRIBUTE):
      - utf8mb4_unicode_ci  : PAD SPACE → '휠 ' = '휠' 이 참
      - utf8mb4_0900_ai_ci  : NO PAD    → '휠 ' = '휠' 이 거짓
    등록이 "이미 있음"을 판정하는 기준은 item_suggestions.item_name의 유니크 인덱스
    (0900_ai_ci)이므로 진단·마이그레이션도 그 기준으로 봐야 한다. 사전에 '휠 '만 있고
    '휠'은 없는 상태는 도달 가능하다 — POST/PUT /api/items는 item_name을 strip하지 않는다.
    그 상태를 unicode_ci로 보면 "이미 등록됨"으로 보여 발산이 숨는다.
    """
    job_id = _seed_included_pair(db_conn, "휠")
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO item_suggestions (item_name) VALUES ('휠 ')"))

    def matches(collation):
        with db_conn.begin() as conn:
            return conn.execute(
                text(
                    "SELECT COUNT(*) FROM training_pairs tp "
                    "JOIN item_suggestions it "
                    f"  ON it.item_name = tp.canonical_label COLLATE {collation} "
                    "WHERE tp.job_id = :j"
                ),
                {"j": job_id},
            ).scalar()

    assert matches("utf8mb4_0900_ai_ci") == 0, "NO PAD: '휠 '는 '휠'과 별개 항목이어야 한다"
    assert matches("utf8mb4_unicode_ci") == 1, "PAD SPACE: 반대 방향은 발산을 숨긴다"
