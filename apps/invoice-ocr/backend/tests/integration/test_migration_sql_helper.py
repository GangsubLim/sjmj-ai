"""tests/integration/_migration_sql.py — 공유 헬퍼의 계약 테스트.

헬퍼 파일은 `_` 접두사라 pytest가 수집하지 않으므로 계약 테스트는 별도 모듈이 필요하다.
이 테스트가 특정 마이그레이션 모듈(과거에는 test_migration_010_item_vocabulary)에
얹혀 있으면 그 모듈이 은퇴할 때 스플리터가 조용히 무보증이 된다 — 실측(2026-08-05):
스플리터를 `--foo`까지 주석으로 걷어내도록 망가뜨린 뒤 010 모듈만 제외하고 돌리면
550건이 전부 초록이었다.
"""

from tests.integration._migration_sql import statements


def test_statement_splitter_matches_mysql_comment_rule():
    """`--` 뒤에 공백이 와야 주석이다 — `--foo`는 MySQL에서 SQL이다.

    헬퍼가 운영보다 관대하면(`--foo`도 주석 취급) 테스트만 초록이고
    `mysql < file`은 syntax error가 난다.
    """
    assert statements("-- 주석\nSELECT 1;") == ["SELECT 1"]
    assert statements("--\nSELECT 1;") == ["SELECT 1"]
    assert statements("--주석아님\nSELECT 1;") == ["--주석아님\nSELECT 1"]
