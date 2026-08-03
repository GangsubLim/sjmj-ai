"""db/migration_010_sync_item_vocabulary.sql — 파일 자체를 읽어 실행해 조건·멱등을 고정한다.

SQL을 테스트에 복사하지 않는다(#40 spec §7) — 복사본은 파일과 갈린다.
한정 없는 `id = id`는 INSERT … SELECT에서 ERROR 1052로 죽는 것을 로컬에서 재현했으므로,
이 테스트가 그 회귀의 방어선이다.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION = _REPO_ROOT / "db" / "migration_010_sync_item_vocabulary.sql"

pytestmark = pytest.mark.usefixtures("db_conn")


def _statements(sql: str) -> list[str]:
    """`--` 주석 줄을 걷어낸 뒤 세미콜론으로 나눈다.

    이 헬퍼는 실제 러너(`scripts/migrate-db.sh:98` — `mysql_do < "$path"`, 파일 전체를
    클라이언트에 먹임)를 **근사**한다. 지원하는 것은 `--` 주석뿐이다 — `#` 주석,
    `/* */` 블록 주석, `DELIMITER`, 그리고 공백 없는 `--`(MySQL은 SQL로 파싱한다)가
    마이그레이션에 들어오면 테스트만 조용히 갈린다.

    주석 판정을 `-- `(뒤 공백) 또는 단독 `--`로 좁히는 이유: MySQL은 `--` 뒤에 공백이
    와야 주석으로 친다. `--foo`까지 주석으로 걷어내면 테스트는 초록인데 실제
    `mysql < file`은 syntax error가 나는, 운영보다 관대한 방향의 오차가 생긴다.

    주석을 먼저 걷어내는 이유: 마이그레이션 헤더의 ROLLBACK 안내는 SQL 예시를 담아
    주석 안에 세미콜론이 있다(migration_009 참조). 그대로 split하면 주석 조각이
    실행 가능한 문장으로 오인된다.
    """
    body = "\n".join(
        line
        for line in sql.splitlines()
        if not (line.lstrip().startswith("-- ") or line.strip() == "--")
    )
    return [s for s in body.split(";") if s.strip()]


def _apply(engine) -> None:
    with engine.begin() as conn:
        for stmt in _statements(_MIGRATION.read_text(encoding="utf-8")):
            conn.execute(text(stmt))


def _names(engine) -> list[str]:
    with engine.begin() as conn:
        return sorted(conn.execute(text("SELECT item_name FROM item_suggestions")).scalars())


def _seed_job(engine, *, reviewed):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/m.jpg', :r)"
            ),
            {"r": reviewed},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


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


def test_statement_splitter_matches_mysql_comment_rule():
    """`--` 뒤에 공백이 와야 주석이다 — `--foo`는 MySQL에서 SQL이다.

    헬퍼가 운영보다 관대하면(`--foo`도 주석 취급) 테스트만 초록이고
    `mysql < file`은 syntax error가 난다.
    """
    assert _statements("-- 주석\nSELECT 1;") == ["SELECT 1"]
    assert _statements("--\nSELECT 1;") == ["SELECT 1"]
    assert _statements("--주석아님\nSELECT 1;") == ["--주석아님\nSELECT 1"]


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

    assert _names(db_conn) == ["라이닝1조"]  # TRIM 적용, 빈 값은 제외


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
