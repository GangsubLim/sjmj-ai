"""구조화 성공 응답 — PHP Response:: 동형.

DB 행의 date/datetime/Decimal 등을 jsonable_encoder로 직렬화(date→'YYYY-MM-DD').
"""

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def list_response(data: list, pagination: dict) -> JSONResponse:
    """페이지네이션 메타를 포함한 컬렉션 성공 응답을 생성한다."""
    return JSONResponse(
        status_code=200,
        content=jsonable_encoder({"success": True, "data": data, "pagination": pagination}),
    )


def single(data) -> JSONResponse:
    """단일 자원 성공 응답(200)을 생성한다."""
    return JSONResponse(status_code=200, content=jsonable_encoder({"success": True, "data": data}))


def created(data) -> JSONResponse:
    """자원 생성 성공 응답(201)을 생성한다."""
    return JSONResponse(status_code=201, content=jsonable_encoder({"success": True, "data": data}))


def deleted(message: str) -> JSONResponse:
    """삭제 성공 응답(200, data=null)을 생성한다."""
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": None, "message": message},
    )
