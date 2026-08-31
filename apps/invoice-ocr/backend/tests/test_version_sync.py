"""버전 단일 진실원 불변식 — 루트 VERSION == app.config.APP_VERSION == api-spec info.version."""

import json
from pathlib import Path

from app.config import APP_VERSION

# 백엔드 패키지 기준 레포 루트: app/config.py → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_version_file_matches_app_version() -> None:
    """루트 VERSION 파일과 APP_VERSION이 일치해야 한다(release.sh 동기 갱신 보장)."""
    version_file = _REPO_ROOT / "VERSION"
    assert version_file.is_file(), f"missing VERSION at {version_file}"
    assert version_file.read_text().strip() == APP_VERSION


def test_api_spec_info_version_matches_app_version() -> None:
    """api-spec.json info.version도 같은 값이어야 한다(sync-version.sh가 함께 갱신, #33)."""
    spec_file = _REPO_ROOT / ".claude" / "ai-context" / "api-spec.json"
    assert spec_file.is_file(), f"missing api-spec at {spec_file}"
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    assert spec["info"]["version"] == APP_VERSION
