"""sjmj-ai invoice-ocr 백엔드 — SP0 최소 셸(/health + 정적 dist 서빙)."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_VERSION, get_static_dir


def health() -> dict[str, str]:
    """헬스체크 — 상태와 버전 반환(SP5에서 마이그레이션 버전 체크로 확장)."""
    return {"status": "ok", "version": APP_VERSION}


def _mount_static(application: FastAPI) -> None:
    """frontend/dist가 있으면 /assets 정적 서빙 + SPA fallback을 마운트한다.

    health 라우트가 먼저 등록되므로 catch-all보다 우선 매칭된다.
    dist가 없으면(개발/빌드 전) 아무것도 마운트하지 않아 API만 노출된다.
    """
    static_dir = get_static_dir()
    if static_dir is None:
        return

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_file = static_dir / "index.html"

    @application.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        """API/정적 미매칭 GET → 실파일 우선, 없으면 SPA index.html."""
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_relative_to(static_dir.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리."""
    application = FastAPI(title="sjmj-ai invoice-ocr API", version=APP_VERSION)
    application.add_api_route("/health", health, methods=["GET"])
    application.add_api_route("/api/health", health, methods=["GET"])
    _mount_static(application)
    return application


app = create_app()
