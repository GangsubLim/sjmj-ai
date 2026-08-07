"""백엔드 설정 — 환경변수 경계 검증(시스템 경계 입력).

`Settings`(pydantic-settings)는 신규 DB 연결 + ML 이음새 env를 담는다.
env 자동 매핑: db_host→DB_HOST 등(대소문자 무시). 빈 비밀번호('')도 유효한 값으로
존중한다. ML 이음새(SJMJ_DATA_DIR·SJMJ_DB_BACKUP)는
자리만 두고 Phase 2가 소비.

`get_port`/`get_static_dir`은 SP0 셸의 기존 동작을 그대로 유지(os.environ 직접 read).
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.10.0"


class Settings(BaseSettings):
    """환경변수에서 DB 연결 + ML 이음새 설정을 로드한다."""

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
    """캐시된 Settings 싱글톤을 반환한다."""
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
    candidate = Path(raw) if raw else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def data_root() -> Path:
    """데이터 루트. SJMJ_DATA_DIR 미설정·부재는 운영 오설정이므로 명확히 실패시킨다.

    실재 검증(is_dir)까지 하는 이유: 소비처 `ocr_service._upload_root()`가
    `mkdir(parents=True)`를 하므로, 오타·상대경로가 들어오면 실패 대신 프로세스 CWD 기준으로
    새 디렉터리가 조용히 생기고 워커 데이터 루트와 어긋난다(업로드는 200인데 crop/warped 조회만
    전부 404). 같은 env를 읽는 ml/ocr_poc/config.py:data_dir()과 경계 엄격도를 맞춘다.

    Settings.sjmj_data_dir를 경유하지 않고 os.environ을 직접 읽는다 — get_settings()가
    @lru_cache라 프로세스 수명 내내 첫 값을 고정하는데, 이 함수는 요청/테스트마다 현재 env를
    반영해야 한다(테스트가 monkeypatch.setenv로 데이터 루트를 갈아끼운다).

    Returns:
        SJMJ_DATA_DIR이 가리키는 디렉터리 Path.

    Raises:
        RuntimeError: SJMJ_DATA_DIR이 비었거나 디렉터리가 아닐 때.
    """
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — 데이터 경로 조립 불가")
    root = Path(raw)
    if not root.is_dir():
        raise RuntimeError(f"SJMJ_DATA_DIR 경로 없음: {root}")
    return root


def crop_dir(job_id: int) -> Path:
    """잡의 crop 산출물 디렉터리($SJMJ_DATA_DIR/ocr_crops/job-{id}).

    이 레이아웃의 생산자는 ML 워커(ml/worker/main.py → handwriting/infer_job.py)다. 백엔드는
    소비자일 뿐이라 조립 지점을 여기 하나로 모은다 — 워커가 레이아웃을 바꾸면 백엔드는 예외
    없이 404로 조용히 빠지므로(썸네일만 사라짐) 드리프트가 런타임에 드러나지 않는다.

    Args:
        job_id: OCR 잡 id.

    Returns:
        crop 디렉터리 Path(존재 여부는 확인하지 않는다 — 호출부가 파일 단위로 판정).
    """
    return data_root() / "ocr_crops" / f"job-{job_id}"
