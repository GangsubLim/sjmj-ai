import pytest

from app.db import transaction
from app.repositories.ocr_repository import OcrRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def test_insert_and_find_job():
    repo = OcrRepository()
    job_id = repo.insert_job("/data/ocr_uploads/abc.jpg")
    job = repo.find_job(job_id)
    assert job["id"] == job_id
    assert job["status"] == "pending"
    assert job["image_path"] == "/data/ocr_uploads/abc.jpg"
    assert job["invoice_id"] is None
    assert job["result_json"] is None


def test_find_job_parses_result_json():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    job = repo.find_job(job_id)
    assert job["status"] == "done"
    assert job["result_json"] == {"rows": [], "supply_sum": 0, "warp_ok": True}


def test_link_invoice_succeeds_once_then_returns_zero():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    # 실제 invoice 행이 있어야 FK 통과
    from app.repositories.invoice_repository import InvoiceRepository

    inv_id = InvoiceRepository().insert(td.invoice())
    with transaction():
        first = repo.link_invoice(job_id, inv_id)
    assert first == 1
    with transaction():
        second = repo.link_invoice(job_id, inv_id)
    assert second == 0  # 이미 연결됨 → 조건부 UPDATE 영향행 0


def test_insert_correction():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    from app.repositories.invoice_repository import InvoiceRepository

    inv_id = InvoiceRepository().insert(td.invoice())
    cid = repo.insert_correction(job_id, inv_id, {"lines": [], "rows_added": 0, "rows_dropped": 0})
    assert cid > 0
