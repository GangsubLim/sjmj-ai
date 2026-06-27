"""구조화 에러 — PHP Response::error / HttpResponseException 동형."""
from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def bad_request(message: str, details: dict | None = None) -> None:
    raise AppError(400, "VALIDATION_ERROR", message, details)


def not_found(message: str = "Resource not found") -> None:
    raise AppError(404, "NOT_FOUND", message)


def _error_body(code: str, message: str, details: dict | None) -> dict:
    err: dict = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"success": False, "error": err}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=_error_body(exc.code, exc.message, exc.details),
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=_error_body("SERVER_ERROR", str(exc), None))


def register_error_handlers(app) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
