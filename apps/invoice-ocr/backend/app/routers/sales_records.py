"""sales_records 라우터 — PHP controllers/SalesRecordController.php 동형.

index는 월별 단일 집계({salespeople, records})를 single()로 반환(목록 아님, pagination 없음).
store는 UPSERT(201) — snapshot_name은 서버가 salesperson.name으로 채운다(클라 입력 무시).
검증은 Validator(골든 details·메시지 보존), 에러는 bad_request/not_found.
"""

from fastapi import APIRouter, Body, Request

from app.core import envelope
from app.core.errors import bad_request, not_found
from app.core.validators import Validator
from app.services.sales_records_service import SalesRecordService

router = APIRouter()

_MAX_QUANTITY = 999999999


def _service() -> SalesRecordService:
    return SalesRecordService()


def _qint(request: Request, key: str, default: int) -> int:
    raw = request.query_params.get(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _is_int(value) -> bool:
    """PHP is_int || (is_numeric && (string)(int)x === (string)x) 동형.

    bool은 정수로 보지 않는다(파이썬 isinstance(True, int)는 True이므로 명시 제외).
    정수 문자열("100")은 허용, 소수/비정수("100.5", "abc")는 거부.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        try:
            return str(int(value)) == value
        except ValueError:
            return False
    return False


@router.get("/sales-records")
def index(request: Request):
    year = _qint(request, "year", 0)
    month = _qint(request, "month", 0)
    if year < 1900 or year > 2999 or month < 1 or month > 12:
        bad_request("year/month 이 올바르지 않습니다.", {"year": year, "month": month})
    return envelope.single(_service().get_monthly(year, month))


@router.post("/sales-records")
def store(data: dict = Body(...)):
    Validator().required(data, ["salesperson_id", "work_date", "quantity"]).date_format(
        data, "work_date"
    ).validate_or_fail()

    quantity = data["quantity"]
    if not _is_int(quantity):
        bad_request("quantity는 정수여야 합니다.", {"quantity": "integer required"})
    quantity_int = int(quantity)
    if quantity_int < 0 or quantity_int > _MAX_QUANTITY:
        bad_request(
            "quantity는 0 이상 999,999,999 이하여야 합니다.",
            {"quantity": "out of range"},
        )

    row = _service().upsert_record(
        int(data["salesperson_id"]), data["work_date"], quantity_int
    )
    return envelope.created(row)


@router.delete("/sales-records/{id}")
def destroy(id: int):
    if not _service().delete_record(id):
        not_found("실적 record를 찾을 수 없습니다.")
    return envelope.deleted("삭제되었습니다.")
