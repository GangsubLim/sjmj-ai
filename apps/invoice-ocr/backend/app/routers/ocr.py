"""OCR 잡 업로드·조회·확정. /api/ocr/* (sync def, threadpool)."""

from fastapi import APIRouter, Body, File, UploadFile

from app.core import envelope
from app.core.errors import not_found
from app.core.validators import Validator
from app.services.ocr_service import OcrService

router = APIRouter()


def _service() -> OcrService:
    return OcrService()


def _validate_confirm(data: dict) -> None:
    Validator().required(data, ["issue_date", "recipient"]).date_format(
        data, "issue_date"
    ).non_empty_array(data, "items").validate_or_fail()


@router.post("/ocr/jobs")
def create_job(photo: UploadFile = File(...)):
    """업로드한 사진으로 OCR 잡을 생성한다."""
    content = photo.file.read()  # SpooledTemporaryFile 동기 읽기
    return envelope.created(_service().create_job(content, photo.filename or ""))


@router.get("/ocr/jobs/{id}")
def get_job(id: int):
    """OCR 잡을 ID로 조회한다."""
    job = _service().get_job(id)
    if job is None:
        not_found("OCR 잡을 찾을 수 없습니다.")
    return envelope.single(job)


@router.post("/ocr/jobs/{id}/confirm")
def confirm(id: int, data: dict = Body(...)):
    """OCR 인식 결과를 확정해 거래명세서로 저장한다."""
    _validate_confirm(data)
    return envelope.single(_service().confirm(id, data))
