"""SP0 백엔드 설정 — 환경변수 로딩(시스템 경계 입력 검증)."""
import os
from pathlib import Path

APP_VERSION = "0.1.0"


def get_port() -> int:
    """SJMJ_PORT 환경변수를 정수로 반환(기본 8400, 비정상 입력은 기본값)."""
    try:
        return int(os.environ.get("SJMJ_PORT", "8400"))
    except ValueError:
        return 8400


def get_static_dir() -> Path | None:
    """프론트 빌드 산출물 디렉터리. 존재할 때만 Path, 없으면 None.

    SJMJ_STATIC_DIR 우선, 없으면 backend 옆 frontend/dist 추정.
    """
    raw = os.environ.get("SJMJ_STATIC_DIR")
    candidate = Path(raw) if raw else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None
