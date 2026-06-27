"""구조화 성공 응답 — PHP Response:: 동형."""
from fastapi.responses import JSONResponse


def list_response(data: list, pagination: dict) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": data, "pagination": pagination},
    )


def single(data) -> JSONResponse:
    return JSONResponse(status_code=200, content={"success": True, "data": data})


def created(data) -> JSONResponse:
    return JSONResponse(status_code=201, content={"success": True, "data": data})


def deleted(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": None, "message": message},
    )
