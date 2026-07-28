"""label_source 화이트리스트 동기 불변식 — app.schemas.ocr.LABEL_SOURCES == api-spec.json enum."""

import json
from pathlib import Path

from app.schemas.ocr import LABEL_SOURCES

# 백엔드 패키지 기준 레포 루트: tests/test_label_source_sync.py → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_label_sources_match_api_spec_enum() -> None:
    """LABEL_SOURCES를 바꾸면 api-spec.json의 label_source enum도 함께 갱신돼야 한다."""
    spec_path = _REPO_ROOT / ".claude/ai-context/api-spec.json"
    spec = json.loads(spec_path.read_text())
    enum = spec["components"]["schemas"]["OcrConfirmRequest"]["properties"]["items"]["items"][
        "properties"
    ]["label_source"]["enum"]
    assert set(enum) == LABEL_SOURCES
