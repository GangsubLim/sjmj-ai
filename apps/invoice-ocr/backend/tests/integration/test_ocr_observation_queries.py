import pytest
from sqlalchemy import text

from app.db import connection
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.ocr_service import OcrService
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    # crop_dir()가 SJMJ_DATA_DIR을 요구한다(config.data_root는 미설정 시 예외).
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def _job_ids(rows):
    return [int(r["job_id"]) for r in rows]


def test_lists_unconfirmed_job_newest_first():
    repo = OcrRepository()
    first = repo.insert_job("/a.jpg")
    second = repo.insert_job("/b.jpg")
    repo.update_result(second, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})

    rows, total = repo.list_unconfirmed(20, 0)

    assert total == 2
    # created_at DESC, id DESC — 같은 초에 들어와도 id로 결정된다.
    assert _job_ids(rows) == [second, first]


def test_extracts_json_scalars_without_pulling_result_json():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(
        job_id,
        "done",
        {
            "rows": [{"row_index": 0}, {"row_index": 1}],
            "supply_sum": 0,
            "warp_ok": False,
        },
    )

    rows, _ = repo.list_unconfirmed(20, 0)
    row = rows[0]

    assert row["status"] == "done"
    assert row["rows_type"] == "ARRAY"
    assert int(row["row_count"]) == 2
    assert row["warp_ok"] == "false"
    assert row["error"] is None
    # payload 폭발 방지 — result_json 전체는 실어 오지 않는다.
    assert "result_json" not in row


def test_extracts_error_string_for_failed_job():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(job_id, "failed", {"error": "warp 실패"})

    rows, _ = repo.list_unconfirmed(20, 0)

    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "warp 실패"


def test_paginates_with_limit_and_offset():
    repo = OcrRepository()
    ids = [repo.insert_job(f"/{i}.jpg") for i in range(3)]

    page1, total = repo.list_unconfirmed(2, 0)
    page2, _ = repo.list_unconfirmed(2, 2)

    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1
    assert _job_ids(page1) + _job_ids(page2) == sorted(ids, reverse=True)


# --- 통합 테스트 ① 여집합 정의 ---


def test_confirmed_job_is_excluded():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(
        job_id,
        "done",
        {
            "rows": [
                {
                    "crop_ref": f"job-{job_id}/row-0",
                    "item_top5": [{"label": "삼겹살", "sim": 0.8}],
                    "supply": 100000,
                }
            ],
            "supply_sum": 100000,
            "warp_ok": True,
        },
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = f"job-{job_id}/row-0"
    OcrService().confirm(job_id, payload)

    rows, total = repo.list_unconfirmed(20, 0)

    assert total == 0
    assert rows == []


def test_confirmed_job_stays_excluded_after_invoice_delete():
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(
        job_id,
        "done",
        {
            "rows": [
                {
                    "crop_ref": f"job-{job_id}/row-0",
                    "item_top5": [{"label": "삼겹살", "sim": 0.8}],
                    "supply": 100000,
                }
            ],
            "supply_sum": 100000,
            "warp_ok": True,
        },
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = f"job-{job_id}/row-0"
    out = OcrService().confirm(job_id, payload)

    # 명세서 hard DELETE — ocr_jobs.invoice_id FK가 ON DELETE SET NULL이라
    # invoice_id 조건만으로는 잡이 되살아난다(invoice_repository.py:175-180).
    assert InvoiceRepository().delete(out["invoice_id"]) is True
    assert repo.find_job(job_id)["invoice_id"] is None

    rows, total = repo.list_unconfirmed(20, 0)

    assert total == 0, "확정 후 명세서를 지운 잡이 확정 전 목록에 되살아났습니다"
    assert rows == []


# --- 통합 테스트 ② 학습쌍 0개 회귀 (ocr_corrections predicate 없으면 실패) ---


def test_confirmed_job_without_training_pairs_stays_excluded_after_invoice_delete():
    """전 행 수동 입력(crop_ref 없음) → 학습쌍 0개 → 확정 → 명세서 삭제 → 여전히 제외.

    build_training_pairs가 crop_ref 없는 행을 건너뛰므로 학습쌍이 0개다. 명세서를 지우면
    invoice_id·training_pairs 두 조건이 동시에 풀린다 — ocr_corrections predicate만이
    이 잡을 막는다.
    """
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": False})

    payload = td.invoice_with_items()  # items에 crop_ref가 없다 = 전 행 수동 입력
    out = OcrService().confirm(job_id, payload)

    with connection() as conn:
        pair_count = conn.execute(
            text("SELECT COUNT(*) FROM training_pairs WHERE job_id = :j"), {"j": job_id}
        ).scalar()
    assert pair_count == 0, "이 테스트의 전제(학습쌍 0개)가 깨졌습니다"

    assert InvoiceRepository().delete(out["invoice_id"]) is True

    with connection() as conn:
        correction_job_id = conn.execute(
            text("SELECT job_id FROM ocr_corrections WHERE job_id = :j"), {"j": job_id}
        ).scalar()
    assert correction_job_id == job_id, "ocr_corrections.job_id가 명세서 삭제를 견디지 못했습니다"

    rows, total = repo.list_unconfirmed(20, 0)

    assert total == 0, "학습쌍 0개 확정 잡이 명세서 삭제 후 확정 전 목록에 되살아났습니다"
    assert rows == []


# --- 통합 테스트 ③ result_json 계약 위반의 JSON 추출 ---


def _force_result_json(job_id: int, result_json: str | None) -> None:
    # repo.update_result는 dict를 json.dumps하므로 NULL·비배열 rows를 못 만든다 → raw SQL.
    # 리터럴을 문자열 보간하면 SQLAlchemy text()가 JSON의 콜론을 bind param으로 파싱한다
    # (실측: '{"rows":null}'은 bindparams에 'null'이 잡혀 InvalidRequestError). bind param 하나로 닫는다.
    with connection() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET status = 'done', result_json = :r WHERE id = :id"),
            {"r": result_json, "id": job_id},
        )


def test_rows_null_is_distinguishable_from_zero_rows():
    repo = OcrRepository()
    null_rows = repo.insert_job("/null.jpg")
    empty_rows = repo.insert_job("/empty.jpg")
    _force_result_json(null_rows, '{"rows": null, "warp_ok": true}')
    repo.update_result(empty_rows, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})

    rows, _ = repo.list_unconfirmed(20, 0)
    by_id = {int(r["job_id"]): r for r in rows}

    # rows: null → JSON_TYPE 'NULL'. JSON_LENGTH는 NULL이 아니라 1을 준다(MySQL 9.6.0 실측).
    # 즉 row_count만으로는 "1행 검출"과 구별되지 않는다 — rows_type이 유일한 판별자다.
    assert by_id[null_rows]["rows_type"] == "NULL"
    assert int(by_id[null_rows]["row_count"]) == 1
    # rows: [] → ARRAY/0. 이것이 진짜 "0행 검출"이며 위와 갈라져야 한다.
    assert by_id[empty_rows]["rows_type"] == "ARRAY"
    assert int(by_id[empty_rows]["row_count"]) == 0


def test_missing_result_json_yields_null_scalars():
    repo = OcrRepository()
    job_id = repo.insert_job("/none.jpg")
    _force_result_json(job_id, None)

    rows, _ = repo.list_unconfirmed(20, 0)

    assert rows[0]["rows_type"] is None
    assert rows[0]["row_count"] is None
    assert rows[0]["warp_ok"] is None


def test_training_pairs_alone_excludes_job_without_corrections():
    """ocr_corrections 없이 training_pairs만 있어도 제외돼야 한다 — predicate 단독 고정 테스트.

    이 잡은 invoice_id NULL·ocr_corrections 없음 상태를 유지한 채 training_pairs만 raw SQL로
    직접 심는다(invoice_id는 NULL 유지). ocr_repository.py의 training_pairs NOT EXISTS 줄을
    지우면 다른 두 predicate(invoice_id·ocr_corrections)는 여전히 통과하므로 이 테스트만 깨진다.
    """
    repo = OcrRepository()
    job_id = repo.insert_job("/a.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})

    with connection() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs (crop_ref, job_id, row_index, canonical_label) "
                "VALUES (:crop_ref, :job_id, 0, '삼겹살')"
            ),
            {"crop_ref": f"job-{job_id}/row-0", "job_id": job_id},
        )

    rows, total = repo.list_unconfirmed(20, 0)

    assert total == 0
    assert rows == []


def test_non_boolean_warp_ok_and_object_rows_are_extracted_verbatim():
    repo = OcrRepository()
    job_id = repo.insert_job("/weird.jpg")
    _force_result_json(job_id, '{"rows": {"a": 1}, "warp_ok": "1"}')

    rows, _ = repo.list_unconfirmed(20, 0)

    assert rows[0]["rows_type"] == "OBJECT"
    assert rows[0]["warp_ok"] == "1"
