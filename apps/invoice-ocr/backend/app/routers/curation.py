"""curation 라우터 — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지. /api/curation/*.

이미지 2종(image/{kind}·crop/{row})은 FileResponse raw 바이트로 success envelope의
명시적 예외(api-conventions.md 참조). 그 외는 표준 envelope.
"""

from fastapi import APIRouter

from app.core import envelope
from app.services.curation_service import CurationService

router = APIRouter()

_LIMIT_MAX = 100


def _service() -> CurationService:
    return CurationService()


@router.get("/curation/jobs")
def list_jobs(page: int = 1, limit: int = 20):
    """검수 큐(confirmed 잡) 목록을 페이지 조회한다."""
    page = max(1, page)
    limit = max(1, min(_LIMIT_MAX, limit))
    jobs, total = _service().list_jobs(page, limit)
    total_pages = (total + limit - 1) // limit if total else 1
    return envelope.list_response(
        jobs, {"page": page, "limit": limit, "total": total, "totalPages": total_pages}
    )
