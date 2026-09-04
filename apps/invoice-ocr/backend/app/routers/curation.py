"""curation 라우터 — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지. /api/curation/*.

전표 이미지(image/{kind})는 FileResponse raw 바이트로 success envelope의
명시적 예외(api-conventions.md 참조). 그 외는 표준 envelope.
"""

from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core import envelope
from app.repositories.items_repository import ItemRepository
from app.schemas.curation import CurationPairPatch, CurationReviewRequest
from app.services.curation_service import CurationService

router = APIRouter()


class ImageKind(StrEnum):
    """원본/워프 전표 이미지 종류."""

    original = "original"
    warped = "warped"


_LIMIT_MAX = 100

# 목록 페이지 번호 상한. ocr 라우터의 _PAGE_MAX와 같은 값·같은 이유 — 상한 없이
# offset=(page-1)*limit을 계산하면 거대 page가 MySQL BIGINT 범위를 넘겨 1064 SQL
# 문법 오류 + SQL 전문 노출로 500이 샌다. 400이 아니라 기존 무음 clamp 의미론을 따른다.
_PAGE_MAX = 1_000_000_000


def _service() -> CurationService:
    # 검수완료 시 included 정식 라벨을 자동완성 사전에 등록(부수효과 — ADR 0008)
    return CurationService(item_repo=ItemRepository())


@router.get("/curation/jobs")
def list_jobs(page: int = 1, limit: int = 20, row_delta: bool = False):
    """검수 큐(confirmed 잡) 목록을 페이지 조회한다(row_delta=true면 행 증감 잡만)."""
    page = max(1, min(_PAGE_MAX, page))
    limit = max(1, min(_LIMIT_MAX, limit))
    jobs, total = _service().list_jobs(page, limit, row_delta=row_delta)
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
    """학습쌍의 status 또는 canonical_label을 갱신한다(잡 세대 토큰 대조)."""
    # exclude_none=True: status/canonical_label을 null로 명시 전송해도 SET NULL 쿼리가 발행되지 않도록 차단.
    # status는 NOT NULL VARCHAR, canonical_label은 min_length=1 — null 덮어쓰기 의미 없음.
    fields = patch.model_dump(exclude_unset=True, exclude_none=True)
    # 토큰은 갱신 필드가 아니라 사전 조건이라 화이트리스트에서 떼어 서비스에 따로 넘긴다.
    job_token = fields.pop("job_token")
    return envelope.single(_service().patch_pair(id, fields, job_token))


@router.post("/curation/jobs/{job_id}/review")
def review(job_id: int, body: CurationReviewRequest):
    """잡을 검수 완료로 표시한다(미처리 쌍 reviewed_at 스탬프 · 잡 세대 토큰 대조)."""
    return envelope.single(_service().mark_reviewed(job_id, body.job_token))


@router.post("/curation/jobs/{job_id}/reprocess")
def reprocess(job_id: int):
    """확정 완료된 잡을 현재 엔진으로 다시 판정하도록 큐에 넣는다(초안은 보존)."""
    return envelope.single(_service().request_reprocess(job_id))


@router.get("/curation/jobs/{job_id}/geometry")
def geometry(job_id: int):
    """단계 기하 사이드카를 조회한다(부재 404 · 손상 500 · 이전 세대 409)."""
    return envelope.single(_service().stage_geometry(job_id))


@router.get("/curation/jobs/{job_id}/image/{kind}")
def image(job_id: int, kind: ImageKind):
    """원본/워프 전표 이미지를 raw 바이트로 반환한다(envelope 예외)."""
    svc = _service()
    if kind is ImageKind.original:
        return FileResponse(svc.original_image(job_id))
    return FileResponse(svc.warped_image(job_id), media_type="image/png")
