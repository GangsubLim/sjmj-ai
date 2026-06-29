import io

import pytest

from app.repositories.ocr_repository import OcrRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def test_create_job_accepts_multipart_and_returns_201(client):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("scan.jpg", io.BytesIO(b"\xff\xd8\xff x"), "image/jpeg")},
    )
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["status"] == "pending"
    assert isinstance(b["data"]["job_id"], int)


def test_get_job_returns_done_with_result(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.get(f"/api/ocr/jobs/{job_id}")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["status"] == "done"
    assert b["data"]["result"]["warp_ok"] is True


def test_get_job_404(client):
    r = client.get("/api/ocr/jobs/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_confirm_creates_invoice(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r.status_code == 200
    assert r.json()["data"]["invoice_id"] > 0


def test_confirm_validation_error(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json={"recipient": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_confirm_twice_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    assert client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload).status_code == 200
    r2 = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"


def test_confirm_pending_job_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")  # status=pending, result_json 없음
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=td.invoice_with_items())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"
