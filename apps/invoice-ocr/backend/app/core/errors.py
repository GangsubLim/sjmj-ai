"""구조화 에러 응답."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """구조화 에러 응답으로 변환되는 애플리케이션 예외."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        """HTTP 상태·에러 코드·메시지·세부정보를 담아 예외를 초기화한다."""
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def bad_request(message: str, details: dict | None = None) -> None:
    """400 검증 에러를 발생시킨다."""
    raise AppError(400, "VALIDATION_ERROR", message, details)


def not_found(message: str = "Resource not found") -> None:
    """404 리소스 없음 에러를 발생시킨다."""
    raise AppError(404, "NOT_FOUND", message)


def conflict(message: str) -> None:
    """409 충돌 에러를 발생시킨다."""
    raise AppError(409, "CONFLICT", message)


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


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: dict = {}
    for err in exc.errors():
        loc = err.get("loc") or ("body",)
        # 중첩/배열 body는 loc[-1]만 쓰면 서로 다른 필드(rows.0.label vs rows.1.label)가
        # 같은 키로 충돌해 setdefault가 일부 에러를 삼킨다. "body"를 뺀 경로를 키로 보존한다.
        parts = [str(p) for p in loc if p != "body"] or ["body"]
        field = ".".join(parts)
        details.setdefault(field, err.get("msg", "유효하지 않은 값입니다."))
    return JSONResponse(
        status_code=400,
        content=_error_body("VALIDATION_ERROR", "검증에 실패했습니다.", details),
    )


def register_error_handlers(app) -> None:
    """앱에 AppError·검증 실패·미처리 예외 핸들러를 등록한다."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
