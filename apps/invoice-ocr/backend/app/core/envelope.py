"""구조화 성공 응답 — PHP Response:: 동형.

DB 행의 date/datetime/Decimal 등을 jsonable_encoder로 직렬화(date→'YYYY-MM-DD').
"""

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def list_response(data: list, pagination: dict) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=jsonable_encoder(
            {"success": True, "data": data, "pagination": pagination}
        ),
    )


def single(data) -> JSONResponse:
    return JSONResponse(
        status_code=200, content=jsonable_encoder({"success": True, "data": data})
    )


def created(data) -> JSONResponse:
    return JSONResponse(
        status_code=201, content=jsonable_encoder({"success": True, "data": data})
    )


def deleted(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": None, "message": message},
    )
