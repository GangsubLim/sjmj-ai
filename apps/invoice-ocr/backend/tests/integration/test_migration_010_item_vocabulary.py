"""db/migration_010_sync_item_vocabulary.sql — 파일 자체를 읽어 실행해 조건·멱등을 고정한다.

SQL을 테스트에 복사하지 않는다(#40 spec §7) — 복사본은 파일과 갈린다.
한정 없는 `id = id`는 INSERT … SELECT에서 ERROR 1052로 죽는 것을 로컬에서 재현했으므로,
이 테스트가 그 회귀의 방어선이다.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration import _migration_sql

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_010_sync_item_vocabulary.sql"

pytestmark = pytest.mark.usefixtures("db_conn")


def _apply(engine) -> None:
    _migration_sql.apply(engine, _MIGRATION)


def _names(engine) -> list[str]:
    with engine.begin() as conn:
        return sorted(conn.execute(text("SELECT item_name FROM item_suggestions")).scalars())


def _seed_job(engine, *, reviewed: int) -> int:
    return _migration_sql.seed_job(engine, reviewed=reviewed)


def _seed_pair(engine, job_id, row_index, label, status="included"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                "VALUES (:r, :j, :i, :l, :l, :s)"
            ),
            {
                "r": f"job-{job_id}/row-{row_index}",
                "j": job_id,
                "i": row_index,
                "l": label,
                "s": status,
            },
        )


def test_migration_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_registers_only_included_labels_of_reviewed_jobs(db_conn):
    reviewed = _seed_job(db_conn, reviewed=1)
    unreviewed = _seed_job(db_conn, reviewed=0)
    _seed_pair(db_conn, reviewed, 0, "휠")
    _seed_pair(db_conn, reviewed, 1, "배제품목", status="excluded")
    _seed_pair(db_conn, unreviewed, 0, "미검수품목")

    _apply(db_conn)

    assert _names(db_conn) == ["휠"]
    # 기본 단위는 ItemsRepository.ensure_exists(default_unit='EA')와 같아야 한다 —
    # 갈리면 등록 경로에 따라 같은 품목의 단위가 달라지는 조용한 발산이 된다.
    with db_conn.begin() as conn:
        unit = conn.execute(
            text("SELECT default_unit FROM item_suggestions WHERE item_name = '휠'")
        ).scalar()
    assert unit == "EA"


def test_skips_null_empty_and_whitespace_labels(db_conn):
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, None)
    _seed_pair(db_conn, job_id, 1, "")
    _seed_pair(db_conn, job_id, 2, "   ")
    _seed_pair(db_conn, job_id, 3, "  라이닝1조  ")

    _apply(db_conn)

    assert _names(db_conn) == ["라이닝1조"]  # 앞뒤 공백 제거 후 빈 값은 제외


def test_is_idempotent_on_second_run(db_conn):
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "중고")
    _seed_pair(db_conn, job_id, 1, "배선수리")

    _apply(db_conn)
    first = _names(db_conn)
    _apply(db_conn)

    assert _names(db_conn) == first == ["배선수리", "중고"]


def test_does_not_touch_existing_rows(db_conn):
    """이미 있는 항목의 usage_count·last_used·category는 보존된다."""
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "휠")
    with db_conn.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO item_suggestions (item_name, default_unit, category, usage_count, "
                "last_used) VALUES ('휠', 'SET', '부품', 9, '2026-01-01 00:00:00')"
            )
        )

    _apply(db_conn)

    with db_conn.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT default_unit, category, usage_count FROM item_suggestions "
                    "WHERE item_name = '휠'"
                )
            )
            .mappings()
            .first()
        )
    assert (row["default_unit"], row["category"], row["usage_count"]) == ("SET", "부품", 9)


def test_migration_normalization_matches_service_strip(db_conn):
    """마이그레이션 정규화가 CurationService._register_label의 .strip()과 같아야 한다.

    TRIM()을 쓰면 탭만 있는 라벨이 공백뿐인 항목으로 사전에 등록되고, '\\t중고'가 서비스가
    넣는 '중고'와 두 항목으로 갈린다(로컬 sjmj_test 재현, 2026-08-03 — 원안 SQL은
    ['\\t', '\\t중고', '\\u3000휠', '라이닝1조']를 등록했다).

    's중고s'는 정규식을 '^\\s+|\\s+$'로 되돌리는 회귀의 방어선이다. MySQL 문자열 리터럴이
    백슬래시를 소비해 그 정규식은 '^s+|s+$'가 되고, 앞뒤 알파벳 s를 지워 's중고s'가
    '중고'로 뭉개진다.
    """
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "\t")
    _seed_pair(db_conn, job_id, 1, "\t중고")
    _seed_pair(db_conn, job_id, 2, "　휠")
    _seed_pair(db_conn, job_id, 3, "s중고s")

    _apply(db_conn)

    assert _names(db_conn) == ["s중고s", "중고", "휠"]


def test_regexp_replace_leaves_0x1c_which_python_strip_removes(db_conn):
    """[[:space:]]와 Python .strip()이 갈리는 유일한 문자군(0x1C..0x1F)을 고정한다.

    헤더가 열거하는 일치 문자군은 위 테스트가 덮는다. 이 테스트는 그 반대편 —
    Python .strip()은 지우지만 ICU [[:space:]]는 White_Space로 보지 않아 남기는
    0x1C..0x1F — 를 명시적으로 고정한다(2026-08-03 로컬 MySQL 9.6 HEX 비교 실측).

    '=' 비교로는 이 차이가 보이지 않는다 — 0x1C..0x1F가 collation-ignorable이라
    지워지지 않았는데도 같다고 나온다. 그래서 Python 쪽 원문 문자열로 단언한다.

    이 잔여 오차가 사전을 쪼개지 않는다는 점도 함께 고정한다: ignorable이라
    유니크 인덱스가 '\\x1c중고'와 '중고'를 같은 항목으로 보고(항목 1건),
    '\\x1c'만 있는 라벨은 <> '' 가드가 걸러낸다.
    """
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "\x1c중고")
    _seed_pair(db_conn, job_id, 1, "\x1c")
    _seed_pair(db_conn, job_id, 2, "\x1f배선수리")

    _apply(db_conn)

    # 0x1C가 남는다 — Python .strip()이었다면 ['배선수리', '중고']였을 것이다.
    assert _names(db_conn) == ["\x1c중고", "\x1f배선수리"]
    # 그럼에도 유니크 인덱스는 '중고'와 같은 항목으로 본다 — 사전이 쪼개지지 않는다.
    with db_conn.begin() as conn:
        same = conn.execute(
            text("SELECT COUNT(*) FROM item_suggestions WHERE item_name = '중고'")
        ).scalar()
    assert same == 1


def test_distinct_collates_on_the_destination_unique_index(db_conn):
    """COLLATE의 *방향*을 고정한다 — 지우거나 utf8mb4_unicode_ci로 되돌리면 깨진다.

    utf8mb4_unicode_ci는 UCA 4.0.0이라 보조평면(supplementary plane) 문자에 가중치가
    없어 '휠𠀀' = '휠𠀁'로 보지만, 목적지 유니크 인덱스의 utf8mb4_0900_ai_ci는 둘을
    구분한다(2026-08-03 로컬 MySQL 9.6 실측).

    따라서 소스 기준(또는 COLLATE 누락)으로 DISTINCT하면 두 라벨이 하나로 뭉개져 한쪽이
    조용히 사라지고, 목적지 기준이면 둘 다 등록된다. 목적지가 별개 항목으로 보는 라벨을
    등록해야 하므로 후자가 맞다.

    다른 라벨 쌍으로는 이 방향이 고정되지 않는다 — 목적지가 더 *넓게* 같다고 보는 쌍은
    DISTINCT를 어느 쪽으로 하든 ON DUPLICATE KEY UPDATE가 흡수해 최종 행 집합이 같다.
    """
    job_id = _seed_job(db_conn, reviewed=1)
    _seed_pair(db_conn, job_id, 0, "휠𠀀")
    _seed_pair(db_conn, job_id, 1, "휠𠀁")

    _apply(db_conn)

    assert _names(db_conn) == ["휠𠀀", "휠𠀁"]
