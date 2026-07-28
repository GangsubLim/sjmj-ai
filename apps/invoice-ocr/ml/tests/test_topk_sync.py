"""TOPK 동기 불변식 — ml의 TOPK 2곳 == api-spec.json label_source의 candidate_picked 개수.

어긋나면 ml이 backend 화이트리스트 밖 rank의 후보를 내보내 confirm 전체가 400으로 죽거나,
반대로 후보가 조용히 유실된다. backend/frontend 쪽 동기는 각자의 테스트가 덮으므로 여기서는
'ml ↔ 계약(api-spec.json)'만 본다 — ml은 별도 uv 패키지라 backend 모듈을 import할 수 없다.
"""

import json
import re
from pathlib import Path

from tools import bank_update

_ML = Path(__file__).resolve().parents[1]
# ml/tests/test_topk_sync.py → tests → ml → invoice-ocr → apps → repo 루트
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _spec_candidate_rank_count() -> int:
    """api-spec.json의 label_source enum에서 candidate_picked:N 개수를 센다."""
    spec = json.loads((_REPO_ROOT / ".claude/ai-context/api-spec.json").read_text())
    enum = spec["components"]["schemas"]["OcrConfirmRequest"]["properties"]["items"]["items"][
        "properties"
    ]["label_source"]["enum"]
    return sum(1 for v in enum if v.startswith("candidate_picked:"))


def _infer_photo_topk() -> int:
    """handwriting/infer_photo.py의 TOPK를 소스 텍스트에서 읽는다.

    그 모듈은 최상단에서 torch를 import해 CI(paddle-free venv)에서 import할 수 없다. 여기서
    필요한 것은 상수값 하나뿐이라 실행 없이 텍스트로 읽는다.
    """
    src = (_ML / "handwriting/infer_photo.py").read_text()
    m = re.search(r"^TOPK = (\d+)$", src, re.MULTILINE)
    assert m is not None, "handwriting/infer_photo.py에서 최상위 TOPK 상수를 찾지 못했다"
    return int(m.group(1))


def test_ml_topk_matches_api_spec_candidate_ranks():
    # dict로 비교하는 이유는 실패 메시지에 어느 파일이 어긋났는지 그대로 찍히게 하려는 것.
    expected = _spec_candidate_rank_count()
    actual = {
        "handwriting/infer_photo.py": _infer_photo_topk(),
        "tools/bank_update.py": bank_update.TOPK,
    }
    assert actual == dict.fromkeys(actual, expected)
