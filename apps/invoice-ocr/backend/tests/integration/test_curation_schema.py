"""training_pairs 스키마 + ocr_jobs.curation_reviewed가 테스트 하니스에 반영됐는지 검증."""

import pytest
from sqlalchemy import text

from tests.integration import _migration_sql

pytestmark = pytest.mark.usefixtures("db_conn")


def test_training_pairs_insert_and_readback(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/x.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, draft_label, final_label, canonical_label, supply, status) "
                "VALUES (:r, :j, 0, '삼겹살', '목살', '목살', 120000, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
        row = (
            conn.execute(
                text(
                    "SELECT crop_ref, status, canonical_label, reviewed_at FROM training_pairs WHERE job_id = :j"
                ),
                {"j": job_id},
            )
            .mappings()
            .first()
        )
    assert row["crop_ref"] == f"job-{job_id}/row-0"
    assert row["status"] == "included"
    assert row["canonical_label"] == "목살"
    assert row["reviewed_at"] is None


def test_ocr_jobs_curation_reviewed_defaults_false(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/y.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        reviewed = conn.execute(
            text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert reviewed == 0


def test_training_pairs_exclusion_reason_defaults_null(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/z.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs (crop_ref, job_id, row_index, status) "
                "VALUES (:r, :j, 0, 'included')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
        reason = conn.execute(
            text("SELECT exclusion_reason FROM training_pairs WHERE job_id = :j"), {"j": job_id}
        ).scalar()
    assert reason is None


def test_training_pairs_exclusion_reason_stores_blank_crop(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/w.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        conn.execute(
            text(
                "INSERT INTO training_pairs (crop_ref, job_id, row_index, status, exclusion_reason) "
                "VALUES (:r, :j, 0, 'excluded', 'blank_crop')"
            ),
            {"r": f"job-{job_id}/row-0", "j": job_id},
        )
        reason = conn.execute(
            text("SELECT exclusion_reason FROM training_pairs WHERE job_id = :j"), {"j": job_id}
        ).scalar()
    assert reason == "blank_crop"


def test_ocr_jobs_curation_reviewed_at_defaults_null(db_conn):
    with db_conn.begin() as conn:
        conn.execute(text("INSERT INTO ocr_jobs (status, image_path) VALUES ('done', '/v.jpg')"))
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        stamp = conn.execute(
            text("SELECT curation_reviewed_at FROM ocr_jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert stamp is None


def test_ocr_jobs_curation_reviewed_at_column_type_matches_production_ddl(db_conn):
    """하니스의 컬럼 정의가 운영 DDL(db/migration_011)과 같은 타입·nullable이어야 한다.

    NULL 기본값만 단언하면 fixtures/schema_test.sql이 운영과 갈려도 아무 데서도
    안 걸린다 — 실측(2026-08-05): 이 컬럼을 VARCHAR(32)로 바꿔도 스위트 559건이
    전부 통과했다.
    """
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
    assert (col["DATA_TYPE"], col["IS_NULLABLE"]) == ("datetime", "YES")


def test_training_pairs_draft_supply_column_type_matches_production_ddl(db_conn):
    """하니스의 컬럼 정의가 운영 DDL(db/migration_012)과 같은 타입·nullable이어야 한다.

    값만 단언하면 fixtures/schema_test.sql이 운영과 갈려도 아무 데서도 안 걸린다 —
    이 컬럼이 BIGINT로 갈리면 범위 가드(DRAFT_SUPPLY_MAX)가 의미를 잃는다.

    DATA_TYPE만으로는 부호를 못 본다 — INT와 INT UNSIGNED가 똑같이 'int'다. DRAFT_SUPPLY_MAX는
    signed 상한이라, unsigned로 갈리면 2147483648..4294967295가 조용히 버려지는데 단언은
    초록으로 남는다. COLUMN_TYPE은 8.0.19 미만에서 표시폭(int(11))이 붙으므로 등가 비교
    대신 'unsigned' 포함 여부만 본다(CI는 mysql:8, 로컬은 9.x).
    """
    with db_conn.begin() as conn:
        col = (
            conn.execute(
                text(
                    "SELECT DATA_TYPE, COLUMN_TYPE, IS_NULLABLE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'training_pairs' "
                    "AND column_name = 'draft_supply'"
                )
            )
            .mappings()
            .first()
        )
    assert col is not None
    assert (col["DATA_TYPE"], col["IS_NULLABLE"]) == ("int", "YES")
    assert "unsigned" not in col["COLUMN_TYPE"].lower()


def test_ocr_jobs_updated_at_column_contract_matches_production_ddl(db_conn):
    """하니스의 updated_at 정의가 운영 DDL(migration_013 — 같은 PR의 Task 3)과 같아야 한다.

    행동 테스트(test_curation_job_token)가 통과해도 하니스만 밀리초이고 운영이 초로 남으면
    운영에서만 같은 초 창이 살아남는다. 정밀도 한 축만 단언하면 형제 테스트가 실측으로 남긴
    교훈(2026-08-05: 컬럼을 VARCHAR(32)로 바꿔도 스위트 559건이 전부 통과)을 반복한다 —
    타입·정밀도·nullable·ON UPDATE를 함께 못 박아 운영 DDL과의 정합을 고정한다.

    4축 단언은 _migration_sql.assert_ocr_jobs_updated_at_contract 공유 헬퍼다 —
    test_migration_013…(마이그레이션이 만든 컬럼)와 같은 축을 본다. 둘 중 하나가 갈리면
    (하니스 vs. 운영 DDL) 반드시 어느 한쪽이 RED가 된다.
    """
    _migration_sql.assert_ocr_jobs_updated_at_contract(db_conn)
