"""salespeople 라우터.

index는 가짜 pagination(page=1, limit=total=count, totalPages=1)을
컨트롤러 레벨에서 구성한다. 검증은 Validator(required name), 응답은 구조화 envelope.
"""

from fastapi import APIRouter, Body

from app.core import envelope
from app.core.errors import not_found
from app.core.validators import Validator
from app.services.salespeople_service import SalespersonService

router = APIRouter()


def _service() -> SalespersonService:
    return SalespersonService()


@router.get("/salespeople")
def index():
    """영업사원 전체 목록을 조회한다."""
    items = _service().get_list()
    count = len(items)
    return envelope.list_response(
        items,
        {
            "page": 1,
            "limit": count,
            "total": count,
            "totalPages": 1,
        },
    )


@router.post("/salespeople")
def store(data: dict = Body(...)):
    """영업사원을 생성한다."""
    Validator().required(data, ["name"]).validate_or_fail()
    return envelope.created(_service().create(data))


@router.put("/salespeople/{id}")
def update(id: int, data: dict = Body(...)):
    """영업사원을 수정한다."""
    Validator().required(data, ["name"]).validate_or_fail()
    sp = _service().update(id, data)
    if not sp:
        not_found("영업사원을 찾을 수 없습니다.")
    return envelope.single(sp)


@router.delete("/salespeople/{id}")
def destroy(id: int):
    """영업사원을 soft-delete(비활성화)한다."""
    if not _service().soft_delete(id):
        not_found("영업사원을 찾을 수 없습니다.")
    return envelope.deleted("비활성화되었습니다.")
