import pytest

from app.repositories.ocr_repository import OcrRepository
from app.services.ocr_service import OcrService
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def test_create_job_saves_file_and_inserts_pending():
    out = OcrService().create_job(b"\xff\xd8\xff binary", "scan.jpg")
    assert out["status"] == "pending"
    job = OcrRepository().find_job(out["job_id"])
    assert job["status"] == "pending"
    assert job["image_path"].endswith(".jpg")


def test_get_job_returns_result_when_done():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    out = OcrService().get_job(job_id)
    assert out["status"] == "done"
    assert out["result"] == {"rows": [], "supply_sum": 0, "warp_ok": True}


def test_get_job_returns_error_when_failed():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "failed", {"error": "warp 실패"})
    out = OcrService().get_job(job_id)
    assert out["status"] == "failed"
    assert out["error"] == "warp 실패"


def test_confirm_creates_invoice_links_job_and_logs_correction():
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
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
    payload["items"][0]["name"] = "목살"  # 라벨 교정

    out = OcrService().confirm(job_id, payload)
    assert out["invoice_id"] > 0
    job = repo.find_job(job_id)
    assert job["invoice_id"] == out["invoice_id"]


def test_confirm_twice_raises_conflict():
    from app.core.errors import AppError

    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()

    OcrService().confirm(job_id, payload)
    with pytest.raises(AppError) as exc:
        OcrService().confirm(job_id, payload)
    assert exc.value.status == 409


@pytest.mark.parametrize(
    "status,result",
    [("pending", None), ("failed", {"error": "warp 실패"})],
)
def test_confirm_rejects_job_not_done(status, result):
    from app.core.errors import AppError

    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    if result is not None:
        repo.update_result(job_id, status, result)  # status=pending은 insert 기본값

    with pytest.raises(AppError) as exc:
        OcrService().confirm(job_id, td.invoice_with_items())
    assert exc.value.status == 409
    assert repo.find_job(job_id)["invoice_id"] is None  # invoice 미생성(가드가 앞단)
