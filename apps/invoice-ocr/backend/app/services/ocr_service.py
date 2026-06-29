"""OCR 잡 업로드·조회·확정. confirm은 행잠금 claim으로 중복 invoice 생성을 막는다."""

import os
import uuid
from pathlib import Path

from app import db
from app.core.errors import conflict, not_found
from app.repositories.companies_repository import CompanyRepository
from app.repositories.items_repository import ItemRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.invoice_service import InvoiceService
from app.services.ocr_correction import build_correction


def _upload_root() -> Path:
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — 업로드 저장 경로 없음")
    p = Path(raw) / "ocr_uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


class OcrService:
    """OCR 잡 업로드·조회·확정을 담당하는 서비스."""

    def __init__(self, repo=None, invoice_service=None, *, transaction=None):
        """저장소·invoice_service·트랜잭션 seam을 주입받아 초기화한다."""
        self.repo = repo or OcrRepository()
        self.invoice_service = invoice_service or InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        )
        self._transaction = transaction or db.transaction

    def create_job(self, photo_bytes: bytes, filename: str) -> dict:
        """업로드 이미지를 저장하고 OCR 잡을 생성해 job_id와 상태를 반환한다."""
        suffix = Path(filename or "").suffix.lower() or ".jpg"
        dest = _upload_root() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(photo_bytes)
        job_id = self.repo.insert_job(str(dest))
        return {"job_id": job_id, "status": "pending"}

    def get_job(self, job_id: int) -> dict | None:
        """OCR 잡 상태와 추론 결과(또는 실패 사유)를 조회한다(없으면 None)."""
        job = self.repo.find_job(job_id)
        if job is None:
            return None
        out = {"id": job["id"], "status": job["status"]}
        result = job.get("result_json")
        if job["status"] == "failed":
            out["error"] = (result or {}).get("error", "추론 실패")
        elif result is not None:
            out["result"] = result
        return out

    def confirm(self, job_id: int, payload: dict) -> dict:
        """OCR 잡을 행잠금 claim으로 확정해 거래명세서를 생성하고 교정 이력을 남긴다.

        중복 invoice 생성을 막기 위해 claim_job/link_invoice로 직렬화한다.
        """
        with self._transaction():
            job = self.repo.claim_job(job_id)
            if job is None:
                not_found("OCR 잡을 찾을 수 없습니다.")
            if job["invoice_id"] is not None:
                conflict("이미 확정된 잡입니다.")
            if job["status"] != "done" or job.get("result_json") is None:
                conflict("아직 확정할 수 없는 잡입니다(추론 미완료).")

            # invoice_items에는 crop_ref 컬럼이 없으므로 제거 후 invoice 생성
            invoice_payload = {
                **payload,
                "items": [
                    {k: v for k, v in item.items() if k != "crop_ref"}
                    for item in payload.get("items", [])
                ],
            }
            invoice = self.invoice_service.create(invoice_payload)
            invoice_id = invoice["id"]

            if self.repo.link_invoice(job_id, invoice_id) == 0:
                conflict("이미 확정된 잡입니다.")

            correction = build_correction(job["result_json"] or {}, payload.get("items", []))
            self.repo.insert_correction(job_id, invoice_id, correction)

        return {"invoice_id": invoice_id}
