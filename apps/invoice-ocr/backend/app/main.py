"""sjmj-ai invoice-ocr 백엔드 — SP0 최소 셸(/health + 정적 dist 서빙)."""

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.config import APP_VERSION, get_static_dir
from app.core.errors import register_error_handlers
from app.routers import (
    companies,
    curation,
    invoices,
    items,
    ocr,
    sales_records,
    salespeople,
    settings,
)

# 콘텐츠 해시 파일명이라 내용이 바뀌면 이름도 바뀐다 → 1년 immutable 캐시가 안전하다.
# 전제: vite 기본 해시 네이밍(assets/[name]-[hash][ext]). 해시 없는 파일을 /assets에
# 두면(예: frontend/public/assets/) 이 1년 immutable이 잘못 박힌다 — 코드로 강제하지 않는다.
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
# index.html은 고정명이라 캐시되면 옛 청크 참조가 남는다 → 항상 서버에서 받는다.
INDEX_CACHE_CONTROL = "no-store"


class _ImmutableStaticFiles(StaticFiles):
    """해시 파일명 에셋에 장기 immutable 캐시 헤더를 붙이는 StaticFiles.

    Starlette `StaticFiles.get_response`를 override하는 내부 훅 결합이다. 상한 없는
    `fastapi>=0.115.0` 의존 하에서 이 시그니처가 드리프트하면
    `test_hashed_assets_are_immutable`가 canary로 잡는다.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        """상위 구현의 응답에 Cache-Control(immutable)을 설정한다.

        Args:
            path: 마운트 기준 상대 경로.
            scope: ASGI scope.

        Returns:
            Cache-Control이 설정된 응답(200·206·304 공통).
        """
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        return response


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
        application.mount("/assets", _ImmutableStaticFiles(directory=assets_dir), name="assets")

    index_file = static_dir / "index.html"

    @application.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        """API/정적 미매칭 GET → 실파일 우선, 없으면 SPA index.html."""
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_relative_to(static_dir.resolve()) and candidate.is_file():
            # 이 분기는 캐시 헤더를 붙이지 않는다 — /index.html을 직접 요청하면 여기서 잡혀
            # no-store가 붙지 않는다. SPA 진입은 /로만 이뤄지므로 수용된 구멍이다.
            return FileResponse(candidate)
        return FileResponse(index_file, headers={"Cache-Control": INDEX_CACHE_CONTROL})


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리."""
    application = FastAPI(title="sjmj-ai invoice-ocr API", version=APP_VERSION)
    register_error_handlers(application)
    application.add_api_route("/health", health, methods=["GET"])
    application.add_api_route("/api/health", health, methods=["GET"])
    # API 라우터는 SPA catch-all(_mount_static)보다 먼저 등록되어야 우선 매칭된다.
    application.include_router(invoices.router, prefix="/api")
    application.include_router(ocr.router, prefix="/api")
    application.include_router(curation.router, prefix="/api")
    application.include_router(companies.router, prefix="/api")
    application.include_router(items.router, prefix="/api")
    application.include_router(settings.router, prefix="/api")
    application.include_router(salespeople.router, prefix="/api")
    application.include_router(sales_records.router, prefix="/api")
    _mount_static(application)
    return application


app = create_app()
