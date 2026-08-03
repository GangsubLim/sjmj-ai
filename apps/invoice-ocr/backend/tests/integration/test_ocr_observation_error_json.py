"""result_json의 $.error가 JSON null일 때의 추출 계약(실 MySQL).

JSON_UNQUOTE(JSON_EXTRACT(col,'$.error'))는 값이 JSON null이면 SQL NULL이 아니라
문자열 'null'을 돌려준다(MySQL 9.6.0 실측). rows에 대해 JSON_TYPE으로 막아둔 것과
같은 함정이라 error에도 같은 방어가 필요하다.
"""

import pytest
from sqlalchemy import text

from app.db import connection
from app.repositories.ocr_repository import OcrRepository
from app.services.ocr_service import OcrService

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    # crop_dir()가 SJMJ_DATA_DIR을 요구한다(config.data_root는 미설정 시 예외).
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def _force_result_json(job_id: int, status: str, result_json: str) -> None:
    # repo.update_result는 dict를 json.dumps하므로 그대로 쓸 수 있지만, 상태까지 함께
    # 고정하려고 raw SQL을 쓴다(test_ocr_observation_queries._force_result_json과 같은 이유).
    with connection() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET status = :s, result_json = :r WHERE id = :id"),
            {"s": status, "r": result_json, "id": job_id},
        )


def test_json_null_error_is_extracted_as_sql_null():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    _force_result_json(job_id, "failed", '{"error": null}')

    rows, _ = repo.list_unconfirmed(20, 0)

    # 문자열 'null'이 새면 "실패 사유: null"이 운영자 화면에 그대로 뜬다.
    assert rows[0]["error"] is None


def test_failed_job_with_json_null_error_falls_back_to_default_message():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    _force_result_json(job_id, "failed", '{"error": null}')

    jobs, _ = OcrService().list_unconfirmed(1, 20)

    assert jobs[0]["observation_status"] == "failed"
    assert jobs[0]["error"] == "추론 실패"


def test_error_string_survives_beyond_512_chars():
    """긴 실패 사유가 잘리거나 사라지지 않는다.

    JSON_VALUE는 기본 RETURNING CHAR(512) + NULL ON ERROR라 512자를 넘는 문자열을
    조용히 NULL로 만든다(실측). CASE WHEN + JSON_UNQUOTE로 남겨야 하는 이유다.
    """
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    long_error = "x" * 600
    repo.update_result(job_id, "failed", {"error": long_error})

    rows, _ = repo.list_unconfirmed(20, 0)

    assert rows[0]["error"] == long_error
