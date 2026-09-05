"""process_one_job 단위 테스트 — mock 큐 + tmpdir 실 파일시스템."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from handwriting.amount_read import DegenerateOutputError
from worker.poll import (
    DegenerateWorkerState,
    PollOutcome,
    _swap_crop_dir,
    process_one_job,
)

RESULT = {"rows": [{"row_index": 0, "supply": 3000}], "supply_sum": 3000, "warp_ok": True}
DEMOTED = {"rows": [], "supply_sum": 0, "warp_ok": False}  # 게이트 강등 — Qwen 미호출
SPAM = "!" * 32


def _infer_ok(image_path, crop_dir, job_id, generation):
    """tmp에 크롭을 실제로 쓰는 추론 대역 — 커밋 경로를 끝까지 태운다.

    tmp를 만들지 않는 대역을 쓰면 _swap_crop_dir의 rename이 FileNotFoundError로 죽어
    교체 실패 복구(requeue_for_reprocess)로 조용히 새는데, 그 갈래에서도 commit_job은
    이미 불린 뒤라 "정상 커밋"을 재는 단언이 전부 통과한다(#94).
    """
    Path(crop_dir).mkdir(parents=True, exist_ok=True)
    (Path(crop_dir) / "row-0.png").write_bytes(b"new")
    return RESULT


def _infer_demoted(image_path, crop_dir, job_id, generation):
    """게이트 강등(rows=[]) 결과를 내되 tmp는 정상으로 남기는 추론 대역."""
    Path(crop_dir).mkdir(parents=True, exist_ok=True)
    return DEMOTED


def _queue(job=None, pairs=None):
    q = MagicMock()
    q.claim_next_pending.return_value = job
    q.fetch_pairs.return_value = pairs or []
    return q


def _job(job_id=9, *, is_reprocess=False, generation=0):
    return {
        "id": job_id,
        "image_path": "/x.jpg",
        "is_reprocess": is_reprocess,
        "generation": generation,
    }


def test_no_pending_returns_false():
    assert process_one_job(_queue(None), lambda *a: RESULT, "/tmp/crops", 0).worked is False


def test_infer_writes_into_a_tmp_directory_not_the_live_one(tmp_path):
    """추론은 tmp에 쓴다 — 커밋 전에는 운영 크롭이 한 픽셀도 바뀌지 않는다(ADR 0010)."""
    seen = {}

    def infer(image_path, crop_dir, job_id, generation):
        seen["dir"] = Path(crop_dir)
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    process_one_job(_queue(_job()), infer, tmp_path, 0)

    assert seen["dir"] == tmp_path / "job-9.tmp"
    assert (live / "row-0.png").read_bytes() == b"new", "커밋 후 디렉터리째 교체된다"


def test_stale_crops_disappear_when_fewer_rows_are_detected(tmp_path):
    """검출 행이 줄어든 재처리가 남기던 유령 크롭이 구조적으로 사라진다(§9)."""

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    for i in range(3):
        (live / f"row-{i}.png").write_bytes(b"old")

    process_one_job(_queue(_job(is_reprocess=True)), infer, tmp_path, 0)

    assert sorted(p.name for p in live.iterdir()) == ["row-0.png"]


def test_a_reprocess_with_zero_new_rows_fails_without_touching_pairs(tmp_path):
    """새 행 0건 재처리는 승계를 건너뛰고 초안 보존 실패로 떨어진다(이슈 #92 고려안 1).

    plan_relink는 "새 행 0건 + 옛 쌍 다수"를 전량 미결의 정상 계획으로 취급한다 — 잡 39는
    이 경로로 확정 쌍 3건이 복구 불가능하게 좌표를 잃었다(crop_ref가 orphan-으로 옮겨지고
    이어 붙일 새 행이 없다). 커밋 전에 막아야 옛 초안·좌표·크롭이 전부 그대로 남는다.
    """
    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return DEMOTED

    q = _queue(_job(is_reprocess=True))
    outcome = process_one_job(q, infer, tmp_path, 0)

    assert outcome == PollOutcome(worked=True, qwen_called=False)
    q.mark_failed_keep_result.assert_called_once_with(9)
    q.fetch_pairs.assert_not_called()
    q.commit_job.assert_not_called()
    q.rollback_to_done.assert_not_called()
    q.mark_failed.assert_not_called()
    assert not (tmp_path / "job-9.tmp").exists(), "가드 실패도 반쪽 크롭을 남기지 않는다"
    assert (live / "row-0.png").read_bytes() == b"old"


def test_a_new_job_with_zero_rows_still_commits_normally(tmp_path):
    """가드는 재처리 전용이다 — 신규 잡의 rows=0(게이트 강등)은 정상 커밋 경로다."""
    q = _queue(_job())

    process_one_job(q, _infer_demoted, tmp_path, 0)

    q.commit_job.assert_called_once()
    q.mark_failed_keep_result.assert_not_called()
    q.requeue_for_reprocess.assert_not_called()


def test_commit_receives_the_plan_built_from_new_rows(tmp_path):
    """poll은 계획을 만들지 않는다 — plan_relink가 만들고 여기서는 넘기기만 한다.

    옛 쌍이 존재하는 잡은 재처리뿐이다(신규 잡은 training_pairs가 없어 승계가 no-op).
    is_reprocess=False로 옛 쌍을 돌려주는 큐는 존재할 수 없는 상태였다(#94).

    tmp를 실제로 쓰는 대역을 써 커밋 이후 교체까지 정상 경로로 끝나는 것을 함께 고정한다
    — 그러지 않으면 교체 실패 복구로 새면서도 commit_job 단언은 통과한다.
    """
    from handwriting.relink import OldPair

    q = _queue(_job(is_reprocess=True), pairs=[OldPair(pair_id=1, row_index=0, supply=3000)])

    process_one_job(q, _infer_ok, tmp_path, 0)

    job_id, result, plan = q.commit_job.call_args[0]
    assert (job_id, result) == (9, RESULT)
    assert [r.final_ref for r in plan.relinked] == ["job-9/row-0"]
    q.requeue_for_reprocess.assert_not_called()
    q.mark_failed_keep_result.assert_not_called()
    q.rollback_to_done.assert_not_called()
    assert (tmp_path / "job-9" / "row-0.png").read_bytes() == b"new", "교체까지 끝난다"
    assert not (tmp_path / "job-9.tmp").exists()


def test_inference_failure_of_a_new_job_marks_failed(tmp_path):
    def boom(*a):
        raise RuntimeError("warp explode")

    q = _queue(_job(3))
    assert process_one_job(q, boom, tmp_path, 0).worked is True
    assert "warp explode" in q.mark_failed.call_args[0][1]["error"]
    q.rollback_to_done.assert_not_called()
    q.commit_job.assert_not_called()


def test_inference_failure_of_a_reprocess_rolls_back_to_done(tmp_path):
    """재처리 실패는 failed가 아니다 — 옛 초안·옛 크롭이 그대로 정합이다(§1)."""

    def boom(*a):
        raise RuntimeError("boom")

    q = _queue(_job(3, is_reprocess=True))
    process_one_job(q, boom, tmp_path, 0)

    q.rollback_to_done.assert_called_once_with(3)
    q.mark_failed.assert_not_called()


def test_failed_run_removes_the_tmp_directory_and_keeps_live_crops(tmp_path):
    """실패한 재처리는 어긋난 상태를 영속시키지 않는다."""

    def half_then_boom(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"half")
        raise RuntimeError("boom")

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    process_one_job(_queue(_job(is_reprocess=True)), half_then_boom, tmp_path, 0)

    assert not (tmp_path / "job-9.tmp").exists()
    assert (live / "row-0.png").read_bytes() == b"old"


def test_commit_failure_removes_the_tmp_directory_before_rollback(tmp_path):
    """커밋이 실패하면 파일 교체는 일어나지 않는다(교체는 커밋 이후에만)."""

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")
    q = _queue(_job(is_reprocess=True))
    q.commit_job.side_effect = RuntimeError("deadlock")

    process_one_job(q, infer, tmp_path, 0)

    assert not (tmp_path / "job-9.tmp").exists()
    assert (live / "row-0.png").read_bytes() == b"old"
    q.rollback_to_done.assert_called_once_with(9)


def test_swap_failure_after_commit_requeues_the_job(tmp_path, monkeypatch):
    """커밋 성공 후 교체 실패는 '새 좌표 + 옛 그림'이므로 재처리 대상으로 되돌린다."""

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return RESULT

    monkeypatch.setattr(
        "worker.poll._swap_crop_dir",
        MagicMock(side_effect=OSError("cross-device link")),
    )
    q = _queue(_job(is_reprocess=True))

    assert process_one_job(q, infer, tmp_path, 0).worked is True
    q.requeue_for_reprocess.assert_called_once_with(9)
    q.rollback_to_done.assert_not_called()


def test_a_swap_failure_below_the_cap_requeues_and_counts(tmp_path, monkeypatch):
    """상한 전에는 현행 복구(재처리 큐 복귀) 그대로 — 실패 횟수만 잡별로 센다(이슈 #88)."""

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return RESULT

    monkeypatch.setattr("worker.poll._swap_crop_dir", MagicMock(side_effect=OSError("ENOTEMPTY")))
    q = _queue(_job(is_reprocess=True))
    counters = {}

    process_one_job(q, infer, tmp_path, 0, swap_failures=counters)

    q.requeue_for_reprocess.assert_called_once_with(9)
    q.mark_failed_keep_result.assert_not_called()
    assert counters == {9: 1}


def test_a_swap_failure_at_the_cap_fails_the_job_and_keeps_the_draft(tmp_path, monkeypatch, capsys):
    """상한 도달 시 requeue 대신 초안 보존 실패로 끊는다 — 무한 재추론 점유 차단(이슈 #88).

    .old가 영구히 안 지워지는 잡은 requeue → 같은 지점 실패 루프에 상한이 없어, 잡당
    수십 초의 모델 추론을 무한 반복하며 다른 재처리 잡을 굶긴다. DB는 이미 커밋된 새
    좌표가 정본이고 .old 마커가 남아 재임베딩 가드는 계속 닫혀 있다 — failed가 사람에게
    보이는 유일한 신호다.
    """

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return RESULT

    monkeypatch.setattr("worker.poll._swap_crop_dir", MagicMock(side_effect=OSError("ENOTEMPTY")))
    q = _queue(_job(is_reprocess=True))
    counters = {9: 2}  # 앞선 두 실행이 이미 같은 지점에서 실패

    outcome = process_one_job(q, infer, tmp_path, 0, swap_failures=counters)

    assert outcome.worked is True
    q.mark_failed_keep_result.assert_called_once_with(9)
    q.requeue_for_reprocess.assert_not_called()
    assert "상한" in capsys.readouterr().err


def test_a_successful_swap_resets_the_failure_counter(tmp_path):
    """중간에 교체가 성공하면 카운터를 지운다 — 상한은 연속 실패에만 건다."""

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    q = _queue(_job(is_reprocess=True))
    counters = {9: 2}

    process_one_job(q, infer, tmp_path, 0, swap_failures=counters)

    assert counters == {}
    q.mark_failed_keep_result.assert_not_called()


def test_a_failing_rerun_keeps_the_unfinished_swap_marker_alive(tmp_path):
    """앞선 실행의 미완 교체 마커는 재실행이 실패해도 사라지지 않는다.

    run A가 커밋 성공 후 교체 직전에 죽으면 잔여 tmp가 유일한 마커다(위 death 테스트).
    재처리 run B가 그것을 지운 뒤 추론에 실패하면 최종 상태는 "DB는 run A의 새 좌표,
    파일은 옛 PNG, 마커 없음"이 되어 require_settled_crops도 prune_missing_crops도
    통과한다 — --reembed-job이 "옛 그림 + 새 라벨"을 정식 등록하는 경로가 열린다.
    """
    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")
    leftover = tmp_path / "job-9.tmp"  # run A가 남긴 미완 교체 마커
    leftover.mkdir()
    (leftover / "row-0.png").write_bytes(b"committed")

    def failing_infer(image_path, crop_dir, job_id, generation):
        raise RuntimeError("추론 실패")

    q = _queue(_job(is_reprocess=True))
    process_one_job(q, failing_infer, tmp_path, 0)

    markers = [p.name for p in tmp_path.iterdir() if p.name.startswith("job-9.")]
    assert markers, "미완 교체 마커가 남아야 재임베딩 가드가 이 잡을 거부한다"


def test_leftover_tmp_directory_from_a_previous_crash_is_cleared(tmp_path):
    """앞선 실패가 남긴 tmp가 새 추론 결과와 섞이지 않는다."""
    stale = tmp_path / "job-9.tmp"
    stale.mkdir()
    (stale / "row-7.png").write_bytes(b"stale")

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    process_one_job(_queue(_job()), infer, tmp_path, 0)

    assert sorted(p.name for p in (tmp_path / "job-9").iterdir()) == ["row-0.png"]


# ---------------------------------------------------------------------------
# 교체가 끝나지 않은 잡은 잔여 디렉터리를 마커로 남긴다 (§9 · §11-1)
# ---------------------------------------------------------------------------


def test_swap_failure_leaves_the_old_marker_instead_of_restoring_crops(tmp_path):
    """두 번째 rename이 실패하면 .old를 되돌리지 않는다 — 404가 옛 그림보다 정직하다.

    이 시점에 DB는 이미 새 좌표라, 옛 그림을 제자리에 되돌리면 "새 좌표 + 그럴싸한 옛
    그림"이 영속돼 사람이 이상을 감지하지 못한 채 확정한다. 남은 .old가 "교체가 끝나지
    않았다"는 마커이고, --reembed-job 가드가 이것을 읽는다.
    """
    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    # tmp가 없으므로 첫 rename(live → .old)은 성공하고 두 번째가 FileNotFoundError로 죽는다.
    with pytest.raises(OSError):
        _swap_crop_dir(tmp_path / "job-9.tmp", live)

    assert not live.exists(), "옛 그림을 새 좌표 자리에 되돌리지 않는다"
    assert (tmp_path / "job-9.old" / "row-0.png").read_bytes() == b"old"


def test_death_between_commit_and_swap_leaves_the_tmp_marker(tmp_path, monkeypatch):
    """커밋 성공 후 교체 전에 프로세스가 죽으면 requeue조차 없다 — tmp가 유일한 신호다.

    이 갈래만이 "새 좌표 + 옛 그림"을 파일이 존재하는 채로 남기므로 크롭 존재 검사를
    통과한다. 뱅크 오염이 성립하는 유일한 경로이며, 그래서 잔여 마커 가드가 필요하다.
    """

    def infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    # 교체 구간은 잡 격리 try 밖이고, 그 자리의 except OSError도 SystemExit을 잡지 않는다
    # — 어떤 핸들러에도 걸리지 않고 그대로 빠져나간다(프로세스 사망 모사).
    monkeypatch.setattr("worker.poll._swap_crop_dir", MagicMock(side_effect=SystemExit))
    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")
    q = _queue(_job(is_reprocess=True))

    with pytest.raises(SystemExit):
        process_one_job(q, infer, tmp_path, 0)

    # 죽은 지점이 커밋 **뒤**임을 못 박는다 — 이게 없으면 커밋 전에 죽는 갈래와 구분되지 않는다.
    q.commit_job.assert_called_once()
    # 마커는 존재만이 아니라 **새 크롭을 담은 채로** 남아야 한다. 정리 로직이 끼어들어
    # tmp를 지우면 마커가 사라져 재임베딩 가드가 그대로 통과한다.
    # 교체 전에 죽었으므로 live는 손대지 않은 채여야 한다 — 항진이 아니다(교체를 앞당기는
    # 회귀가 들어오면 여기서 b"new"가 읽힌다).
    assert (live / "row-0.png").read_bytes() == b"old"
    assert (tmp_path / "job-9.tmp" / "row-0.png").read_bytes() == b"new"
    q.requeue_for_reprocess.assert_not_called()
    q.mark_failed_keep_result.assert_not_called()


# ---------------------------------------------------------------------------
# MLX degenerate 대응 (이슈 #99) — 커밋 차단 + 되돌림 + 프로세스 재기동
# ---------------------------------------------------------------------------


def _spam_infer(*_a):
    raise DegenerateOutputError(f"판독기 출력이 degenerate — raw 표본: {SPAM!r}")


class FakeQueue:
    """상태를 들고 있는 큐 대역 — pending 복귀 후 재점유까지 한 시나리오에서 잇는다.

    MagicMock은 claim_next_pending이 항상 같은 값을 돌려줘 '되돌린 잡이 다시 잡히는가'를
    표현할 수 없다. 자가복구 경로 테스트에는 상태가 필요하다.
    """

    def __init__(self, job_id=7, *, is_reprocess=False):
        self.job = {
            "id": job_id,
            "image_path": "/x.jpg",
            "is_reprocess": is_reprocess,
            "generation": 0,
        }
        self.status = "pending"
        self.committed = []

    def claim_next_pending(self):
        if self.status != "pending":
            return None
        self.status = "running"
        return dict(self.job)

    def fetch_pairs(self, job_id):
        return []

    def commit_job(self, job_id, result, plan):
        self.status = "done"
        self.committed.append(result)

    def mark_failed(self, job_id, error_json):
        self.status = "failed"

    def rollback_to_done(self, job_id):
        self.status = "done"

    def requeue_pending(self, job_id):
        self.status = "pending"

    def requeue_for_reprocess(self, job_id):
        self.status = "pending"


def test_degenerate_worker_state_cannot_be_swallowed_by_job_isolation():
    """언어 의미론으로 보장한다 — SystemExit은 except Exception에 걸리지 않는다.

    미래에 광역 핸들러가 추가돼도 이 예외는 흡수될 수 없다(spec §2).
    """
    assert issubclass(DegenerateWorkerState, SystemExit)
    assert not issubclass(DegenerateWorkerState, Exception)
    assert DegenerateWorkerState(1).code == 1


def test_degenerate_after_the_first_qwen_job_requeues_a_new_job_and_exits(tmp_path):
    q = _queue(_job(3))

    with pytest.raises(DegenerateWorkerState) as exc:
        process_one_job(q, _spam_infer, tmp_path, 1)

    assert exc.value.code == 1
    q.requeue_pending.assert_called_once_with(3)
    q.rollback_to_done.assert_not_called()
    q.mark_failed.assert_not_called()
    q.commit_job.assert_not_called()


def test_degenerate_after_the_first_qwen_job_requeues_a_reprocess_job_too_and_exits(tmp_path):
    """재시도 갈래는 신규·재처리를 가르지 않는다(B1-b) — 재처리 잡도 pending으로 되돌린다.

    result_json이 남아 있으므로 다음 점유에서 재처리 잡으로 스스로 재분류된다.
    """
    q = _queue(_job(3, is_reprocess=True))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _spam_infer, tmp_path, 1)

    q.requeue_pending.assert_called_once_with(3)
    q.rollback_to_done.assert_not_called()
    q.mark_failed.assert_not_called()


def test_degenerate_removes_the_tmp_crops_before_exiting(tmp_path):
    """되돌린 잡이 반쪽 크롭을 남기면 다음 실행이 새 그림과 섞는다(기존 실패 경로와 동일 처리)."""

    def half_then_spam(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"half")
        raise DegenerateOutputError(f"raw 표본: {SPAM!r}")

    q = _queue(_job(9))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, half_then_spam, tmp_path, 1)

    assert not (tmp_path / "job-9.tmp").exists()


def test_degenerate_logs_the_job_id_and_a_raw_sample_to_stderr(tmp_path, capsys):
    q = _queue(_job(3))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _spam_infer, tmp_path, 1)

    err = capsys.readouterr().err
    assert "[degenerate] job=3" in err
    assert "!" in err


def test_the_first_degenerate_job_after_boot_retires_the_job_and_exits(tmp_path):
    """크래시루프 가드 — 부팅 직후부터 스팸이면 재기동해도 같은 일이 반복될 뿐이다(spec §3).

    B2-b: 첫 Qwen 잡이어도 워커를 살려두지 않는다 — 잡을 은퇴시키고 함께 종료한다.
    """
    q = _queue(_job(3))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _spam_infer, tmp_path, 0)

    q.mark_failed.assert_called_once()
    assert q.mark_failed.call_args[0][0] == 3
    assert SPAM in q.mark_failed.call_args[0][1]["error"]
    q.requeue_pending.assert_not_called()


def test_the_first_degenerate_reprocess_after_boot_retires_the_job_and_exits(tmp_path):
    q = _queue(_job(3, is_reprocess=True))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _spam_infer, tmp_path, 0)

    q.rollback_to_done.assert_called_once_with(3)
    q.mark_failed.assert_not_called()


def test_a_collapse_mid_job_is_still_treated_as_the_first_qwen_job(tmp_path):
    """H2의 유효 잔여 — 카운터는 잡 단위다. 앞칸은 정상이고 뒤칸에서 뒤늦게 붕괴해도
    process_one_job 호출 자체가 처음이면(qwen_jobs_before=0) '첫 Qwen 잡'으로 취급되어
    은퇴 + 즉시 종료된다(감지는 셀 단위지만 크래시루프 판정은 잡 단위, B2-b로 워커도 함께
    종료하므로 두 번째 잡에서 이어지는 시나리오 자체가 성립하지 않는다).
    """

    def _late_spam_infer(*_a):
        raise DegenerateOutputError(f"27칸 정상 후 붕괴 — raw 표본: {SPAM!r}")

    q = _queue(_job(3))

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _late_spam_infer, tmp_path, 0)

    q.mark_failed.assert_called_once()
    assert q.mark_failed.call_args[0][0] == 3
    q.requeue_pending.assert_not_called()


def test_a_requeued_new_job_is_reclaimed_and_completes_on_the_next_process(tmp_path):
    """자가복구 경로 — pending 복귀 → 재점유 → 정상 결과 → done."""

    def good_infer(image_path, crop_dir, job_id, generation):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    q = FakeQueue(7)

    with pytest.raises(DegenerateWorkerState):
        process_one_job(q, _spam_infer, tmp_path, 1)
    assert q.status == "pending", "되돌려진 잡이 큐에 남아야 새 프로세스가 다시 집는다"

    outcome = process_one_job(q, good_infer, tmp_path, 0)  # 재기동한 프로세스 모사

    assert outcome == PollOutcome(worked=True, qwen_called=True)
    assert q.status == "done"
    assert q.committed == [RESULT]


def test_a_normal_result_with_rows_counts_as_a_qwen_call(tmp_path):
    q = _queue(_job())

    outcome = process_one_job(q, _infer_ok, tmp_path, 0)

    assert outcome == PollOutcome(worked=True, qwen_called=True)
    q.commit_job.assert_called_once()
    q.requeue_for_reprocess.assert_not_called()


def test_a_gate_demoted_result_does_not_count_as_a_qwen_call(tmp_path):
    """게이트 강등(rows=[])은 Qwen을 부르지 않는다 — 카운터를 올리면 크래시루프 가드가 헐거워진다."""
    q = _queue(_job())

    outcome = process_one_job(q, _infer_demoted, tmp_path, 0)

    assert outcome == PollOutcome(worked=True, qwen_called=False)
    q.commit_job.assert_called_once()  # 강등도 커밋된다(계약 불변)
    q.requeue_for_reprocess.assert_not_called()


def test_an_ordinary_failure_does_not_count_as_a_qwen_call(tmp_path):
    """오판의 결과는 재기동 대신 잡 실패 처리라는 보수적 강등이다(spec §3)."""

    def boom(*a):
        raise RuntimeError("warp explode")

    outcome = process_one_job(_queue(_job(3)), boom, tmp_path, 5)

    assert outcome == PollOutcome(worked=True, qwen_called=False)


def test_an_empty_queue_returns_a_no_work_outcome():
    assert process_one_job(_queue(None), lambda *a: RESULT, "/tmp/crops", 3) == PollOutcome(
        worked=False, qwen_called=False
    )


def test_claimed_generation_reaches_the_inference_function(tmp_path):
    """세대는 claim이 집은 값 그대로 추론에 실린다 — 기하 스탬프의 유일한 입력이다."""
    seen = {}

    def infer(image_path, crop_dir, job_id, generation):
        seen["generation"] = generation
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    process_one_job(_queue(_job(generation=7)), infer, tmp_path, 0)

    assert seen["generation"] == 7
