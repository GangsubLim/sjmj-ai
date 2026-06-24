"""sjmj-ai invoice-ocr 백엔드 — SP0 최소 셸(/health)."""
from fastapi import FastAPI

from app.config import APP_VERSION


def health() -> dict[str, str]:
    """헬스체크 — 상태와 버전 반환(SP5에서 마이그레이션 버전 체크로 확장)."""
    return {"status": "ok", "version": APP_VERSION}


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리. Task 3에서 정적 서빙을 여기에 추가한다."""
    application = FastAPI(title="sjmj-ai invoice-ocr API", version=APP_VERSION)
    application.add_api_route("/health", health, methods=["GET"])
    application.add_api_route("/api/health", health, methods=["GET"])
    return application


app = create_app()
