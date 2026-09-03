"""마이그레이션 SQL 파일을 읽어 실 러너처럼 실행하는 공유 테스트 헬퍼.

test_migration_010_item_vocabulary.py와 test_migration_011_curation_reviewed_at.py가
공유한다. 두 파일에 복제하면(과거에 그랬다) 스플리터 계약이 한쪽만 갱신되는 사이
실제 러너(`mysql < file`)와 갈리는 false-green이 생긴다. 그 계약 테스트는 특정
마이그레이션 모듈이 아니라 이 헬퍼 옆(test_migration_sql_helper.py)에 둔다 —
마이그레이션 모듈에 얹어 두면 그 모듈 은퇴와 함께 계약이 조용히 사라진다.

파일명이 `_`로 시작해 pytest가 테스트 모듈로 수집하지 않는다 — 이 파일 자체는 테스트를
담지 않는다.
"""

from pathlib import Path

from sqlalchemy import text


def statements(sql: str) -> list[str]:
    """`--` 주석 줄을 걷어낸 뒤 세미콜론으로 나눈다.

    이 헬퍼는 실제 러너(`scripts/migrate-db.sh:98` — `mysql_do < "$path"`, 파일 전체를
    클라이언트에 먹임)를 **근사**한다. 지원하는 것은 `--` 주석뿐이다 — `#` 주석,
    `/* */` 블록 주석, `DELIMITER`, 공백 없는 `--`(MySQL은 SQL로 파싱한다), 그리고
    **문자열 리터럴 안의 세미콜론**(`';'` — 실 러너는 문장 끝으로 보지 않지만 이
    스플리터는 그 자리에서 쪼갠다)이 마이그레이션에 들어오면 테스트만 조용히 갈린다.
    현재 마이그레이션 중에는 리터럴 세미콜론 사례가 없다.

    주석 판정을 `-- `(뒤 공백) 또는 단독 `--`로 좁히는 이유: MySQL은 `--` 뒤에 공백이
    와야 주석으로 친다. `--foo`까지 주석으로 걷어내면 테스트는 초록인데 실제
    `mysql < file`은 syntax error가 나는, 운영보다 관대한 방향의 오차가 생긴다.

    주석을 먼저 걷어내는 이유: 마이그레이션 헤더의 ROLLBACK 안내는 SQL 예시를 담아
    주석 안에 세미콜론이 있다(migration_009 참조). 그대로 split하면 주석 조각이
    실행 가능한 문장으로 오인된다.

    나눈 문장은 apply()가 드라이버 커서에 파라미터 없이 그대로 넘긴다 — 이유는 apply() 참조.
    """
    body = "\n".join(
        line
        for line in sql.splitlines()
        if not (line.lstrip().startswith("-- ") or line.strip() == "--")
    )
    return [s for s in body.split(";") if s.strip()]


def apply(engine, migration_path: Path) -> None:
    """마이그레이션 문장들을 실제 러너와 같은 방식 — 파라미터 없는 원문 — 으로 실행한다.

    드라이버 커서에 직접 넣는 이유: 마이그레이션 SQL은 파라미터가 없는 완성된 문장이므로,
    중간 계층이 문자열을 해석하면 운영(`mysql < file`)과 오차가 생긴다. 실측(로컬):
      - `text(stmt)`          : `'(주):삼정'`의 `:삼정`을 bind 파라미터로 잡아 실패.
                                (`%`는 통과 — SQLAlchemy가 이스케이프해 준다.)
      - `exec_driver_sql(stmt)`: 빈 파라미터를 함께 넘겨 pymysql(paramstyle=format)이
                                `%`-포맷을 돌리므로 `LIKE '%foo%'`에서 실패.
      - 드라이버 커서 + args 생략: pymysql이 포맷을 아예 건너뛰어 둘 다 통과.
    셋 다 운영에서는 멀쩡한 SQL이므로, 앞의 둘은 테스트만 죽는 false RED다.
    """
    with engine.begin() as conn, conn.connection.cursor() as cursor:
        for stmt in statements(migration_path.read_text(encoding="utf-8")):
            cursor.execute(stmt)


def seed_job(engine, *, reviewed: int) -> int:
    """ocr_jobs에 잡 1건을 심고 새 id를 반환한다."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/m.jpg', :r)"
            ),
            {"r": reviewed},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def assert_ocr_jobs_updated_at_contract(engine) -> None:
    """ocr_jobs.updated_at이 운영 DDL(migration_013) 4축과 같은지 단언한다.

    data_type · datetime_precision · is_nullable · extra(on-update 표기) 4축을 함께
    본다. test_curation_schema.py(하니스가 만든 컬럼)와 test_migration_013…(마이그레이션이
    만든 컬럼)가 이 헬퍼를 공유해 항상 같은 4축을 본다 — 정밀도 한 축만 보면 NOT NULL이나
    ON UPDATE가 갈려도 스위트 전량이 초록으로 남는다. 그 드리프트는 migration_013 자신의
    3축 가드(멱등 ALTER-skip)를 영구 미충족으로 만들어 배포마다 테이블 재작성 ALTER가
    다시 도는 형태로 나타난다.
    """
    with engine.begin() as conn:
        col = (
            conn.execute(
                text(
                    "SELECT DATA_TYPE, DATETIME_PRECISION, IS_NULLABLE, EXTRA "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'ocr_jobs' "
                    "AND column_name = 'updated_at'"
                )
            )
            .mappings()
            .first()
        )
    assert col is not None
    assert (col["DATA_TYPE"], col["DATETIME_PRECISION"]) == ("timestamp", 3)
    assert col["IS_NULLABLE"] == "NO"
    # EXTRA 표기는 버전마다 접두(DEFAULT_GENERATED)와 (3) 표기가 흔들려 부분 문자열만 본다
    # (로컬 9.6.0 실측: 'DEFAULT_GENERATED on update CURRENT_TIMESTAMP(3)').
    assert "on update" in col["EXTRA"].lower()
