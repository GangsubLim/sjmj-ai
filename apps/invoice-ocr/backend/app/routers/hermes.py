"""hermes 라우터 — 위임 입력 현황 요약/목록/대조 상세/사진. /api/hermes/*, 전부 GET.

원본 사진(entries/{id}/photo)은 FileResponse raw 바이트로 success envelope의 명시적
예외다(api-conventions.md의 curation image/{kind} 선례와 동일 취급). 그 외는 표준 envelope.
"""

from fastapi import APIRouter

from app.core import envelope
from app.services.hermes_service import HermesService

router = APIRouter()

_LIMIT_MAX = 100

# 목록 페이지 번호 상한. curation.py의 _PAGE_MAX와 같은 값이다. 이 슬라이스의 목록은 파이썬
# 슬라이스라 거대 offset이 SQL로 새지 않지만, 같은 API 표면에서 clamp 의미론이 슬라이스마다
# 다르면 프론트의 page 상한(use-page-param.PAGE_MAX)이 어느 쪽에 맞춰야 할지 갈린다.
_PAGE_MAX = 1_000_000_000


def _service() -> HermesService:
    return HermesService()


@router.get("/hermes/summary")
def summary():
    """초안 전량의 집계와 판독 지식 상태·버전별 일치율을 조회한다."""
    return envelope.single(_service().summary())
