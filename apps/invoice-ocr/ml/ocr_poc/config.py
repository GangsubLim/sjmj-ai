"""env 기반 경로 해석 — 시스템 경계 입력 검증."""

import os
from pathlib import Path


def data_dir() -> Path:
    """SJMJ_DATA_DIR 루트. 미설정/부재 시 RuntimeError."""
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — .env 참조")
    p = Path(raw)
    if not p.is_dir():
        raise RuntimeError(f"SJMJ_DATA_DIR 경로 없음: {p}")
    return p


def db_backup_path() -> Path:
    """SJMJ_DB_BACKUP (.sql). 미설정/부재 시 RuntimeError."""
    raw = os.environ.get("SJMJ_DB_BACKUP")
    if not raw:
        raise RuntimeError("SJMJ_DB_BACKUP 미설정 — .env 참조")
    p = Path(raw)
    if not p.is_file():
        raise RuntimeError(f"SJMJ_DB_BACKUP 파일 없음: {p}")
    return p


def images_dir() -> Path:
    return data_dir() / "images"


def labels_dir() -> Path:
    return data_dir() / "labels"


def references_dir() -> Path:
    return data_dir() / "references"
