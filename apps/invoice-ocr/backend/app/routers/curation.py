"""curation 라우터 — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지. /api/curation/*.

이미지 2종(image/{kind}·crop/{row})은 FileResponse raw 바이트로 success envelope의
명시적 예외(api-conventions.md 참조). 그 외는 표준 envelope.
"""

from fastapi import APIRouter

from app.core import envelope
from app.schemas.curation import CurationPairPatch
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


@router.get("/curation/jobs/{job_id}")
def job_detail(job_id: int):
    """잡 상세(단계 이미지 신호 + 행별 쌍)를 조회한다."""
    return envelope.single(_service().get_detail(job_id))


@router.patch("/curation/pairs/{id}")
def patch_pair(id: int, patch: CurationPairPatch):
    """학습쌍의 status 또는 canonical_label을 갱신한다."""
    # exclude_none=True: status/canonical_label을 null로 명시 전송해도 SET NULL 쿼리가 발행되지 않도록 차단.
    # status는 NOT NULL VARCHAR, canonical_label은 min_length=1 — null 덮어쓰기 의미 없음.
    return envelope.single(
        _service().patch_pair(id, patch.model_dump(exclude_unset=True, exclude_none=True))
    )


@router.post("/curation/jobs/{job_id}/review")
def review(job_id: int):
    """잡을 검수 완료로 표시한다(미처리 쌍 reviewed_at 스탬프)."""
    return envelope.single(_service().mark_reviewed(job_id))
