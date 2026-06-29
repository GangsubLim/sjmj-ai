"""companies 라우터 — PHP controllers/CompanyController.php 동형.

검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는
sync def라 threadpool에서 실행된다. index/invoices는 가짜 pagination을 쓴다.
"""

from fastapi import APIRouter, Body, Request

from app.core import envelope
from app.core.errors import not_found
from app.core.validators import Validator
from app.services.companies_service import CompanyService

router = APIRouter()

_SORT_BY = ("company_name", "usage_count", "last_used", "created_at")


def _service() -> CompanyService:
    return CompanyService()


def _validate_company(data: dict) -> None:
    Validator().required(data, ["company_name"]).max_length(
        data, "company_name", 100
    ).business_number(data, "business_number").validate_or_fail()


@router.get("/companies")
def index(request: Request):
    sort_by = request.query_params.get("sort_by", "company_name")
    filters = {
        "q": request.query_params.get("q"),
        "sort_by": sort_by if sort_by in _SORT_BY else "company_name",
    }
    result = _service().get_list(filters)
    return envelope.list_response(result["data"], result["pagination"])


@router.get("/companies/{id}")
def show(id: int):
    company = _service().get_by_id(id)
    if not company:
        not_found("거래처를 찾을 수 없습니다.")
    return envelope.single(company)


@router.post("/companies")
def store(data: dict = Body(...)):
    _validate_company(data)
    return envelope.created(_service().create(data))


@router.put("/companies/{id}")
def update(id: int, data: dict = Body(...)):
    _validate_company(data)
    company = _service().update(id, data)
    if not company:
        not_found("거래처를 찾을 수 없습니다.")
    return envelope.single(company)


@router.delete("/companies/{id}")
def destroy(id: int):
    if not _service().delete(id):
        not_found("거래처를 찾을 수 없습니다.")
    return envelope.deleted("거래처가 삭제되었습니다.")


@router.get("/companies/{id}/invoices")
def invoices(id: int):
    rows = _service().get_invoices(id)
    if rows is None:
        not_found("거래처를 찾을 수 없습니다.")
    pagination = {"page": 1, "limit": 9999, "total": len(rows), "totalPages": 1}
    return envelope.list_response(rows, pagination)
