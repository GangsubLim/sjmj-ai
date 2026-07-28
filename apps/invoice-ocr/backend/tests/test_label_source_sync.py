"""label_source 화이트리스트 동기 불변식 — app.schemas.ocr.LABEL_SOURCES == api-spec.json enum.

이 파일이 덮는 것은 backend ↔ 계약(api-spec.json) 한 변뿐이다. 카디널리티의 상류인
ml TOPK(handwriting/infer_photo.py · tools/bank_update.py)는 여기서 검증하지 않는다 —
ml은 별도 uv 패키지라 백엔드 venv에 설치돼 있지 않고(import하면 CI가 깨진다), 소스 파싱으로
중복 구현하면 같은 불변식이 두 곳에 흩어진다. 그 변은 ml/tests/test_topk_sync.py가
같은 api-spec.json enum을 기준으로 덮으며(CI의 ml 잡에서 실행), 프론트 TOP_K는
frontend/src/utils/label-source.test.ts가 덮는다. 세 테스트가 같은 spec을 허브로 삼아
삼각 동기를 이룬다 — 어느 하나라도 빠지면 그 스택의 드리프트가 런타임에서만 발현한다.
"""

import json
from pathlib import Path

from app.schemas.ocr import LABEL_SOURCES

# 백엔드 패키지 기준 레포 루트: tests/test_label_source_sync.py → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_label_sources_match_api_spec_enum() -> None:
    """LABEL_SOURCES를 바꾸면 api-spec.json의 label_source enum도 함께 갱신돼야 한다."""
    spec_path = _REPO_ROOT / ".claude/ai-context/api-spec.json"
    # encoding 명시: api-spec.json은 한글 description이 다수인 비-UTF-8 불가 파일이라,
    # 로케일 기본 인코딩에 맡기면 LANG=C 환경(launchd·self-hosted 러너)에서 드리프트와
    # 무관한 UnicodeDecodeError로 깨진다.
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    enum = spec["components"]["schemas"]["OcrConfirmRequest"]["properties"]["items"]["items"][
        "properties"
    ]["label_source"]["enum"]
    assert set(enum) == LABEL_SOURCES
