"""백엔드 설정 — 환경변수 경계 검증(시스템 경계 입력).

`Settings`(pydantic-settings)는 신규 DB 연결 + ML 이음새 env를 담는다.
env 자동 매핑: db_host→DB_HOST 등(대소문자 무시). 빈 비밀번호('')도 유효한 값으로
존중(PHP config/app.php DB_* 동치). ML 이음새(SJMJ_DATA_DIR·SJMJ_DB_BACKUP)는
자리만 두고 Phase 2가 소비.

`get_port`/`get_static_dir`은 SP0 셸의 기존 동작을 그대로 유지(os.environ 직접 read).
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.2.1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # 런타임 MySQL 연결
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "kslim"
    db_user: str = "kslim"
    db_pass: str = ""

    # ML 이음새(Phase 2 소비, 1B는 자리만)
    sjmj_data_dir: str | None = None
    sjmj_db_backup: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


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
    candidate = (
        Path(raw) if raw else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    return candidate if candidate.is_dir() else None
