"""invoices 라우터 — PHP controllers/InvoiceController.php 동형.

검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는
sync def라 threadpool에서 실행된다(전역 엔진 공유 + service.transaction()의 conn
바인딩은 같은 콜스택이라 안전). export는 envelope 밖 Response(text/csv).
"""

from urllib.parse import quote

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from app.core import envelope
from app.core.errors import bad_request, not_found
from app.core.validators import Validator
from app.repositories.companies_repository import CompanyRepository
from app.repositories.items_repository import ItemRepository
from app.services.export_service import ExportService
from app.services.invoice_service import InvoiceService

router = APIRouter()

_SORT_BY = ("issue_date", "grand_total", "recipient", "created_at")
_SORT_ORDER = ("asc", "desc")


def _service() -> InvoiceService:
    # create/update 시 거래처·품목 usage_count 증가(PHP modern 부수효과 동치)
    return InvoiceService(company_repo=CompanyRepository(), item_repo=ItemRepository())


def _qint(request: Request, key: str, default: int) -> int:
    raw = request.query_params.get(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _validate_invoice(data: dict) -> None:
    Validator().required(data, ["issue_date", "recipient"]).date_format(
        data, "issue_date"
    ).max_length(data, "recipient", 100).max_length(
        data, "vehicle_no", 255
    ).non_empty_array(data, "items").validate_or_fail()


@router.get("/invoices/export")
def export(request: Request) -> Response:
    fmt = request.query_params.get("format", "csv")
    if fmt not in ("csv", "xlsx"):
        bad_request("format은 csv 또는 xlsx만 가능합니다.")
    filters = {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "company_id": request.query_params.get("company_id"),
    }
    try:
        filename, body = ExportService().export_invoices(fmt, filters)
    except ValueError as exc:
        bad_request(str(exc))
    # 한글 파일명은 RFC 5987 filename*=UTF-8''(HTTP 헤더는 latin-1만 허용)
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.get("/invoices")
def index(request: Request):
    sort_by = request.query_params.get("sort_by", "issue_date")
    sort_order = request.query_params.get("sort_order", "desc")
    filters = {
        "page": max(_qint(request, "page", 1), 1),
        "limit": min(max(_qint(request, "limit", 20), 1), 100),
        "search": request.query_params.get("search"),
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "sort_by": sort_by if sort_by in _SORT_BY else "issue_date",
        "sort_order": sort_order if sort_order in _SORT_ORDER else "desc",
    }
    result = _service().get_list(filters)
    return envelope.list_response(result["data"], result["pagination"])


@router.get("/invoices/{id}")
def show(id: int):
    invoice = _service().get_by_id(id)
    if not invoice:
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.single(invoice)


@router.post("/invoices")
def store(data: dict = Body(...)):
    _validate_invoice(data)
    return envelope.created(_service().create(data))


@router.put("/invoices/{id}")
def update(id: int, data: dict = Body(...)):
    _validate_invoice(data)
    invoice = _service().update(id, data)
    if not invoice:
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.single(invoice)


@router.delete("/invoices/{id}")
def destroy(id: int):
    if not _service().delete(id):
        not_found("거래명세서를 찾을 수 없습니다.")
    return envelope.deleted("거래명세서가 삭제되었습니다.")


@router.post("/invoices/{id}/duplicate")
def duplicate(id: int):
    new_invoice = _service().duplicate(id)
    if not new_invoice:
        not_found("원본 거래명세서를 찾을 수 없습니다.")
    return envelope.created(new_invoice)
