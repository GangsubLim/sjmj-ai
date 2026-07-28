"""OCR 잡 업로드·조회·확정. /api/ocr/* (sync def, threadpool)."""

from fastapi import APIRouter, File, UploadFile

from app.core import envelope
from app.core.errors import not_found
from app.schemas.ocr import OcrConfirmRequest
from app.services.ocr_service import OcrService

router = APIRouter()


def _service() -> OcrService:
    return OcrService()


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
def confirm(id: int, data: OcrConfirmRequest):
    """OCR 인식 결과를 확정해 거래명세서로 저장한다."""
    # exclude_unset: 미전송 optional(crop_ref/label_source)이 None으로 주입되면 전환 전 dict와
    # 키 집합이 달라진다. extra 필드는 그대로 보존된다.
    return envelope.single(_service().confirm(id, data.model_dump(exclude_unset=True)))
