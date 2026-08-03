"""OCR 잡 업로드·조회·확정. /api/ocr/* (sync def, threadpool).

crop 이미지 엔드포인트는 FileResponse raw 바이트로 success envelope의 명시적 예외다
(api-conventions.md 참조).
"""

from fastapi import APIRouter, File, Path, UploadFile
from fastapi.responses import FileResponse

from app.core import envelope
from app.core.errors import not_found
from app.schemas.ocr import OcrConfirmRequest
from app.services.ocr_service import OcrService

router = APIRouter()

# crop 행 번호 상한. 실제 거래명세서 한 장의 행 수는 수십 줄 이내라 이 값을 크게 상회하는
# 요청은 비정상이다 — 상한 없이 서버가 (job_id, row) 정수로 파일명을 조립하면, 매우 긴 숫자
# row가 파일시스템 파일명 길이 한계(통상 255바이트)를 넘겨 OSError → 500 + 절대경로 노출로
# 이어질 수 있다(품질 리뷰 재현). ge=0으로 음수도 함께 400으로 흡수한다.
_MAX_CROP_ROW = 9999

# 목록 페이지 크기 상한. curation 라우터의 _LIMIT_MAX와 같은 값·같은 이유(payload 상한).
_LIMIT_MAX = 100

# 목록 페이지 번호 상한. 상한 없이 offset=(page-1)*limit을 계산하면 거대 page 값이 MySQL
# BIGINT 범위를 넘겨 1064 SQL 문법 오류 + SQL 전문 노출로 500이 샌다(품질 리뷰 재현). 정상
# 사용 범위를 크게 상회하는 값이므로 400이 아니라 기존 clamp 의미론 그대로 무음 clamp한다.
_PAGE_MAX = 1_000_000_000


def _service() -> OcrService:
    return OcrService()


@router.post("/ocr/jobs")
def create_job(photo: UploadFile = File(...)):
    """업로드한 사진으로 OCR 잡을 생성한다."""
    content = photo.file.read()  # SpooledTemporaryFile 동기 읽기
    return envelope.created(_service().create_job(content, photo.filename or ""))


@router.get("/ocr/jobs")
def list_unconfirmed_jobs(page: int = 1, limit: int = 20):
    """확정 전(미확정) OCR 잡 목록을 관측용으로 페이지 조회한다(읽기 전용 — ADR 0009)."""
    page = max(1, min(_PAGE_MAX, page))
    limit = max(1, min(_LIMIT_MAX, limit))
    jobs, total = _service().list_unconfirmed(page, limit)
    total_pages = (total + limit - 1) // limit if total else 1
    return envelope.list_response(
        jobs, {"page": page, "limit": limit, "total": total, "totalPages": total_pages}
    )


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


@router.get("/ocr/jobs/{id}/crop/{row}")
def crop(id: int, row: int = Path(ge=0, le=_MAX_CROP_ROW)):
    """행 crop 이미지를 raw 바이트로 반환한다(envelope 예외)."""
    return FileResponse(_service().crop_image(id, row), media_type="image/png")
