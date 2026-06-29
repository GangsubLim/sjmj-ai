"""items 라우터 — PHP controllers/ItemController.php 동형.

검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는
sync def라 threadpool에서 실행된다. item_name UNIQUE 위반은 service에서 409
DUPLICATE_NAME으로 graceful 처리된다.
"""

from fastapi import APIRouter, Body, Request

from app.core import envelope
from app.core.errors import not_found
from app.core.validators import Validator
from app.services.items_service import ItemService

router = APIRouter()

_SORT_BY = ("item_name", "usage_count", "last_used", "category")


def _service() -> ItemService:
    return ItemService()


def _validate_item(data: dict) -> None:
    Validator().required(data, ["item_name"]).max_length(data, "item_name", 200).validate_or_fail()


@router.get("/items")
def index(request: Request):
    sort_by = request.query_params.get("sort_by", "item_name")
    filters = {
        "q": request.query_params.get("q"),
        "category": request.query_params.get("category"),
        "sort_by": sort_by if sort_by in _SORT_BY else "item_name",
    }
    result = _service().get_list(filters)
    return envelope.list_response(result["data"], result["pagination"])


@router.get("/items/{id}")
def show(id: int):
    item = _service().get_by_id(id)
    if not item:
        not_found("품목을 찾을 수 없습니다.")
    return envelope.single(item)


@router.post("/items")
def store(data: dict = Body(...)):
    _validate_item(data)
    return envelope.created(_service().create(data))


@router.put("/items/{id}")
def update(id: int, data: dict = Body(...)):
    _validate_item(data)
    item = _service().update(id, data)
    if not item:
        not_found("품목을 찾을 수 없습니다.")
    return envelope.single(item)


@router.delete("/items/{id}")
def destroy(id: int):
    if not _service().delete(id):
        not_found("품목을 찾을 수 없습니다.")
    return envelope.deleted("품목이 삭제되었습니다.")
