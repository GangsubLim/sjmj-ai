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
    assert new_rows({"rows": "not-a-list"}) == []


def test_build_plan_equals_the_hand_wired_composition():
    """build_plan은 fetch_pairs + new_rows + plan_relink 조합과 같은 계획을 낸다."""
    pairs = [
        OldPair(pair_id=7, row_index=0, supply=3000, draft_supply=2800),
        OldPair(pair_id=8, row_index=1, supply=9000, draft_supply=8900),
    ]
    queue = _queue(pairs)
    assert build_plan(queue, 5, RESULT) == plan_relink(5, pairs, new_rows(RESULT))
    queue.fetch_pairs.assert_called_once_with(5)


def test_plan_module_stays_importable_from_the_paddle_free_core():
    """worker/plan.py 상단 규약 — 모듈 레벨 의존은 handwriting.relink뿐이다.

    코어 venv(pillow만)에서 import가 깨져도 CI(worker extra 설치됨)는 초록이라
    소스 구조를 직접 고정한다(tests/test_infer_job_gate.py의 지연 import 가드와 같은 수법).
    """
    src = Path(__file__).resolve().parents[1] / "worker" / "plan.py"
    module_level, _ = import_scopes(src)
    assert [n for n in module_level if not n.startswith("handwriting.relink")] == []


def test_the_dryrun_forecast_uses_the_same_plan_production_commits(tmp_path, monkeypatch):
    """같은 엔진·같은 infer 결과에서 두 경로의 RelinkPlan이 구조적으로 같다.

    "같은 함수를 부른다"는 호출 이름 가드로는 인자가 갈리는 드리프트를 못 잡는다
    (tests/test_warp_gate_rows.py가 같은 문제를 다루며 남긴 교훈). #106 이후에도 예측이
    자동으로 따라오게 하는 유일한 고정 장치다.

    엔진을 두 벌 만드는 이유: process_one_job은 commit_job까지 가서 crop_ref·row_index를
    바꾸므로, 같은 엔진에서 뒤이어 예측하면 입력이 이미 달라져 있다.
    """
    from sqlalchemy import create_engine, text

    from tools import reprocess_dryrun as rd
    from worker.db import WorkerQueue
    from worker.poll import process_one_job

    class _CapturingQueue(WorkerQueue):
        """claim_next_pending만 대역 — sqlite는 FOR UPDATE를 파싱하지 못한다(실측:
        OperationalError near "FOR"). 나머지 메서드는 실제 SQL을 그대로 탄다."""

        def __init__(self, engine, job):
            super().__init__(engine)
            self._job = job
            self.committed = []

        def claim_next_pending(self):
            return self._job

        def commit_job(self, job_id, result_json, plan):
            self.committed.append(plan)
            super().commit_job(job_id, result_json, plan)

    def _seeded():
        engine = create_engine("sqlite://", future=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE ocr_jobs (id INTEGER PRIMARY KEY, status TEXT, "
                    "image_path TEXT, result_json TEXT, curation_reviewed INTEGER DEFAULT 1)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE training_pairs (id INTEGER PRIMARY KEY, job_id INTEGER, "
                    "crop_ref TEXT UNIQUE, row_index INTEGER, supply INTEGER, "
                    "draft_supply INTEGER, status TEXT, exclusion_reason TEXT, reviewed_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO ocr_jobs (id, status, image_path, result_json) "
                    "VALUES (5, 'running', '/data/up/5.jpeg', '{}')"
                )
            )
            for pid, (ri, sup) in enumerate([(0, 3000), (1, 7000)], start=1):
                conn.execute(
                    text(
                        "INSERT INTO training_pairs (id, job_id, crop_ref, row_index, supply, "
                        "draft_supply, status) VALUES (:pid, 5, :ref, :ri, :sup, :sup, 'included')"
                    ),
                    {"pid": pid, "ref": f"job-5/row-{ri}", "ri": ri, "sup": sup},
                )
        return engine

    # generation에 기본값을 두는 것은 이 대역만의 편의다 — 아래에서 4-arity(운영 경로,
    # process_one_job)와 3-arity(드라이런 경로, forecast_job) 양쪽에 같은 함수를 재사용한다.
    def infer(image_path, crop_dir, job_id, generation=None):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return RESULT

    production = _CapturingQueue(
        _seeded(),
        {"id": 5, "image_path": "/data/up/5.jpeg", "is_reprocess": True, "generation": 0},
    )
    process_one_job(production, infer, tmp_path, 1)

    dryrun_queue = WorkerQueue(_seeded())
    captured: dict = {}
    real_build_plan = rd.build_plan

    def spy(queue, job_id, result_json):
        plan = real_build_plan(queue, job_id, result_json)
        captured["plan"] = plan
        captured["args"] = (job_id, result_json)
        return plan

    monkeypatch.setattr(rd, "build_plan", spy)
    forecast = rd.forecast_job(dryrun_queue, infer, 5)

    assert captured["args"] == (5, RESULT), "forecast_job이 build_plan에 넘긴 인자까지 고정한다"
    assert production.committed[0] == captured["plan"]
    assert forecast.relinked == len(captured["plan"].relinked)
    assert forecast.orphaned == len(captured["plan"].orphaned)
