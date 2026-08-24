"""worker.plan — 운영(poll)과 드라이런(tools.reprocess_dryrun)이 공유하는 승계 계획 조립."""

from pathlib import Path
from unittest.mock import MagicMock

from handwriting.relink import NewRow, OldPair, plan_relink
from tests.conftest import import_scopes
from worker.plan import build_plan, new_rows

RESULT = {
    "rows": [{"row_index": 0, "supply": 3000}, {"row_index": 1, "supply": 5000}],
    "warp_ok": True,
}


def _queue(pairs):
    q = MagicMock()
    q.fetch_pairs.return_value = pairs
    return q


def test_new_rows_reads_row_index_and_supply():
    assert new_rows(RESULT) == [
        NewRow(row_index=0, supply=3000),
        NewRow(row_index=1, supply=5000),
    ]


def test_new_rows_treats_a_missing_or_malformed_rows_key_as_empty():
    # 전량 미결이 되어 사람에게 드러나는 것이 계약이다(옮겨온 _new_rows docstring).
    assert new_rows({"warp_ok": False}) == []
    assert new_rows({"rows": None}) == []


def test_build_plan_equals_the_hand_wired_composition():
    """build_plan은 fetch_pairs + new_rows + plan_relink 조합과 같은 계획을 낸다."""
    pairs = [
        OldPair(pair_id=7, row_index=0, supply=3000, draft_supply=2800),
        OldPair(pair_id=8, row_index=1, supply=9000, draft_supply=8900),
    ]
    assert build_plan(_queue(pairs), 5, RESULT) == plan_relink(5, pairs, new_rows(RESULT))


def test_plan_module_stays_importable_from_the_paddle_free_core():
    """worker/plan.py 상단 규약 — 모듈 레벨 의존은 handwriting.relink뿐이다.

    코어 venv(pillow만)에서 import가 깨져도 CI(worker extra 설치됨)는 초록이라
    소스 구조를 직접 고정한다(tests/test_infer_job_gate.py의 지연 import 가드와 같은 수법).
    """
    src = Path(__file__).resolve().parents[1] / "worker" / "plan.py"
    module_level, _ = import_scopes(src)
    assert [n for n in module_level if not n.startswith("handwriting.relink")] == []
