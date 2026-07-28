"""OCR 잡 업로드·조회·확정. confirm은 행잠금 claim으로 중복 invoice 생성을 막는다."""

import os
import re
import uuid
from pathlib import Path

from app import db
from app.core.errors import bad_request, conflict, not_found
from app.repositories.companies_repository import CompanyRepository
from app.repositories.curation_repository import CurationRepository
from app.repositories.items_repository import ItemRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.invoice_service import InvoiceService
from app.services.ocr_correction import build_correction, build_training_pairs


def _upload_root() -> Path:
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — 업로드 저장 경로 없음")
    p = Path(raw) / "ocr_uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


_ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
# 저장 경로에 이어붙는 것은 suffix뿐(stem은 폐기)이며 화이트리스트가 주 방어선.
# 제어문자 검사는 이슈 #21 AC가 요구한 심층 방어(비용 0)로,
# salespeople_service._CONTROL_CHAR와 같은 범위다(수정 시 함께 갱신).
_CONTROL_CHAR = re.compile(r"[\x00-\x1F\x7F]")

# invoice_items에 대응 컬럼이 없는 OCR/UI 전용 필드 — invoice 생성 payload에서 걷어낸다.
# (repository가 명시 컬럼만 읽어 SQL에는 닿지 않지만, 경계를 코드로 드러내 둔다.)
_NON_INVOICE_ITEM_KEYS = frozenset({"crop_ref", "label_source"})


def _validated_suffix(filename: str) -> str:
    """업로드 파일명을 검증하고 소문자로 정규화한 확장자를 반환한다.

    Args:
        filename: 클라이언트가 보낸 원본 파일명(신뢰할 수 없는 입력).

    Returns:
        `.jpg` / `.jpeg` / `.png` 중 하나(소문자).

    Raises:
        AppError: 파일명에 제어문자가 있거나 허용되지 않은 확장자일 때 400 VALIDATION_ERROR.
    """
    name = filename or ""
    if _CONTROL_CHAR.search(name):
        bad_request(
            "파일명에 허용되지 않는 문자가 포함되어 있습니다.",
            {"photo": "파일명에 제어문자를 사용할 수 없습니다."},
        )
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        bad_request(
            "jpg/jpeg/png 형식만 업로드할 수 있습니다.",
            {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."},
        )
    return suffix


class OcrService:
    """OCR 잡 업로드·조회·확정을 담당하는 서비스."""

    def __init__(self, repo=None, invoice_service=None, *, transaction=None, curation_repo=None):
        """저장소·invoice_service·트랜잭션 seam·큐레이션 저장소를 주입받아 초기화한다."""
        self.repo = repo or OcrRepository()
        self.invoice_service = invoice_service or InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        )
        self._transaction = transaction or db.transaction
        self.curation_repo = curation_repo or CurationRepository()

    def create_job(self, photo_bytes: bytes, filename: str) -> dict:
        """업로드 이미지를 저장하고 OCR 잡을 생성해 job_id와 상태를 반환한다.

        Raises:
            AppError: 파일명이 허용 확장자(.jpg/.jpeg/.png)가 아니거나 제어문자를 포함할 때 400.
        """
        suffix = _validated_suffix(filename)
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

            # OCR/UI 전용 필드를 제거한 뒤 invoice 생성(_NON_INVOICE_ITEM_KEYS 주석 참조).
            # build_correction은 아래에서 원본 payload를 받는다 — label_source가 살아 있어야 한다.
            invoice_payload = {
                **payload,
                "items": [
                    {k: v for k, v in item.items() if k not in _NON_INVOICE_ITEM_KEYS}
                    for item in payload.get("items", [])
                ],
            }
            invoice = self.invoice_service.create(invoice_payload)
            invoice_id = invoice["id"]

            if self.repo.link_invoice(job_id, invoice_id) == 0:
                conflict("이미 확정된 잡입니다.")

            correction = build_correction(job["result_json"] or {}, payload.get("items", []))
            self.repo.insert_correction(job_id, invoice_id, correction)
            pairs = build_training_pairs(job_id, invoice_id, correction)
            self.curation_repo.insert_training_pairs(pairs)

        return {"invoice_id": invoice_id}
