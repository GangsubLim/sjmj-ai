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

    from sqlalchemy import text

    from app.db import connection

    with connection() as conn:
        correction_count = conn.execute(
            text("SELECT COUNT(*) FROM ocr_corrections WHERE job_id = :j"),
            {"j": job_id},
        ).scalar()
    assert correction_count == 1


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


def test_confirm_missing_job_raises_not_found():
    """존재하지 않는 job_id로 confirm하면 404가 발생해야 한다."""
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        OcrService().confirm(999999, td.invoice_with_items())
    assert exc.value.status == 404


def test_confirm_rollback_on_race_link_returns_zero():
    """link_invoice가 0을 반환(레이싱 confirm)할 때 invoice INSERT가 롤백돼야 한다.

    OcrService의 DI 심을 통해 stub repo를 주입한다:
    - claim_job: done 잡(invoice_id=None)을 반환해 early-guard를 통과시킨다.
    - link_invoice: 0 반환 → conflict 발생 → tx 롤백.
    실제 InvoiceService + 실제 db.transaction을 사용해 invoice INSERT가 정말 롤백되는지 검증한다.
    """
    from sqlalchemy import text

    from app import db
    from app.core.errors import AppError
    from app.repositories.companies_repository import CompanyRepository
    from app.repositories.items_repository import ItemRepository
    from app.services.invoice_service import InvoiceService

    class _StubRepo:
        def claim_job(self, job_id):
            return {
                "id": job_id,
                "status": "done",
                "result_json": {"rows": [], "supply_sum": 0, "warp_ok": True},
                "invoice_id": None,
            }

        def link_invoice(self, job_id, invoice_id):
            return 0  # 레이싱 confirm이 이미 연결함을 시뮬레이션

        def insert_correction(self, job_id, invoice_id, correction_json):
            return 1  # conflict가 먼저 발생해 여기 도달하지 않지만 stub

    service = OcrService(
        repo=_StubRepo(),
        invoice_service=InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        ),
    )

    with pytest.raises(AppError) as exc:
        service.confirm(999, td.invoice_with_items())
    assert exc.value.status == 409

    # invoice INSERT가 트랜잭션 롤백으로 취소됐는지 확인 — orphan 없어야 함
    with db.connection() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM invoices")).scalar()
    assert n == 0, f"롤백 후 invoices 행이 남았습니다 (got {n}). orphan 버그입니다."
