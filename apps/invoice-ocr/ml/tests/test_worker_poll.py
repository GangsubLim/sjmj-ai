"""process_one_job 단위 테스트 — mock 큐 + tmpdir 실 파일시스템."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from worker.poll import _swap_crop_dir, process_one_job

RESULT = {"rows": [{"row_index": 0, "supply": 3000}], "supply_sum": 3000, "warp_ok": True}


def _queue(job=None, pairs=None):
    q = MagicMock()
    q.claim_next_pending.return_value = job
    q.fetch_pairs.return_value = pairs or []
    return q


def _job(job_id=9, *, is_reprocess=False):
    return {"id": job_id, "image_path": "/x.jpg", "is_reprocess": is_reprocess}


def test_no_pending_returns_false():
    assert process_one_job(_queue(None), lambda *a: RESULT, "/tmp/crops") is False


def test_infer_writes_into_a_tmp_directory_not_the_live_one(tmp_path):
    """추론은 tmp에 쓴다 — 커밋 전에는 운영 크롭이 한 픽셀도 바뀌지 않는다(ADR 0010)."""
    seen = {}

    def infer(image_path, crop_dir, job_id):
        seen["dir"] = Path(crop_dir)
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    process_one_job(_queue(_job()), infer, tmp_path)

    assert seen["dir"] == tmp_path / "job-9.tmp"
    assert (live / "row-0.png").read_bytes() == b"new", "커밋 후 디렉터리째 교체된다"


def test_stale_crops_disappear_when_fewer_rows_are_detected(tmp_path):
    """검출 행이 줄어든 재처리가 남기던 유령 크롭이 구조적으로 사라진다(§9)."""

    def infer(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    for i in range(3):
        (live / f"row-{i}.png").write_bytes(b"old")

    process_one_job(_queue(_job(is_reprocess=True)), infer, tmp_path)

    assert sorted(p.name for p in live.iterdir()) == ["row-0.png"]


def test_commit_receives_the_plan_built_from_new_rows(tmp_path):
    """poll은 계획을 만들지 않는다 — plan_relink가 만들고 여기서는 넘기기만 한다."""
    from handwriting.relink import OldPair

    q = _queue(_job(), pairs=[OldPair(pair_id=1, row_index=0, supply=3000)])
    process_one_job(q, lambda *a: RESULT, tmp_path)

    job_id, result, plan = q.commit_job.call_args[0]
    assert (job_id, result) == (9, RESULT)
    assert [r.final_ref for r in plan.relinked] == ["job-9/row-0"]


def test_inference_failure_of_a_new_job_marks_failed(tmp_path):
    def boom(*a):
        raise RuntimeError("warp explode")

    q = _queue(_job(3))
    assert process_one_job(q, boom, tmp_path) is True
    assert "warp explode" in q.mark_failed.call_args[0][1]["error"]
    q.rollback_to_done.assert_not_called()
    q.commit_job.assert_not_called()


def test_inference_failure_of_a_reprocess_rolls_back_to_done(tmp_path):
    """재처리 실패는 failed가 아니다 — 옛 초안·옛 크롭이 그대로 정합이다(§1)."""

    def boom(*a):
        raise RuntimeError("boom")

    q = _queue(_job(3, is_reprocess=True))
    process_one_job(q, boom, tmp_path)

    q.rollback_to_done.assert_called_once_with(3)
    q.mark_failed.assert_not_called()


def test_failed_run_removes_the_tmp_directory_and_keeps_live_crops(tmp_path):
    """실패한 재처리는 어긋난 상태를 영속시키지 않는다."""

    def half_then_boom(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"half")
        raise RuntimeError("boom")

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")

    process_one_job(_queue(_job(is_reprocess=True)), half_then_boom, tmp_path)

    assert not (tmp_path / "job-9.tmp").exists()
    assert (live / "row-0.png").read_bytes() == b"old"


def test_commit_failure_removes_the_tmp_directory_before_rollback(tmp_path):
    """커밋이 실패하면 파일 교체는 일어나지 않는다(교체는 커밋 이후에만)."""

    def infer(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")
    q = _queue(_job(is_reprocess=True))
    q.commit_job.side_effect = RuntimeError("deadlock")

    process_one_job(q, infer, tmp_path)

    assert not (tmp_path / "job-9.tmp").exists()
    assert (live / "row-0.png").read_bytes() == b"old"
    q.rollback_to_done.assert_called_once_with(9)


def test_swap_failure_after_commit_requeues_the_job(tmp_path, monkeypatch):
    """커밋 성공 후 교체 실패는 '새 좌표 + 옛 그림'이므로 재처리 대상으로 되돌린다."""

    def infer(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        return RESULT

    monkeypatch.setattr(
        "worker.poll._swap_crop_dir",
        MagicMock(side_effect=OSError("cross-device link")),
    )
    q = _queue(_job(is_reprocess=True))

    assert process_one_job(q, infer, tmp_path) is True
    q.requeue_for_reprocess.assert_called_once_with(9)
    q.rollback_to_done.assert_not_called()


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

    def failing_infer(image_path, crop_dir, job_id):
        raise RuntimeError("추론 실패")

    q = _queue(_job(is_reprocess=True))
    process_one_job(q, failing_infer, tmp_path)

    markers = [p.name for p in tmp_path.iterdir() if p.name.startswith("job-9.")]
    assert markers, "미완 교체 마커가 남아야 재임베딩 가드가 이 잡을 거부한다"


def test_leftover_tmp_directory_from_a_previous_crash_is_cleared(tmp_path):
    """앞선 실패가 남긴 tmp가 새 추론 결과와 섞이지 않는다."""
    stale = tmp_path / "job-9.tmp"
    stale.mkdir()
    (stale / "row-7.png").write_bytes(b"stale")

    def infer(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    process_one_job(_queue(_job()), infer, tmp_path)

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

    def infer(image_path, crop_dir, job_id):
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return RESULT

    # SystemExit은 BaseException이라 잡 단위 격리 except를 통과한다 — 프로세스 사망 모사.
    monkeypatch.setattr("worker.poll._swap_crop_dir", MagicMock(side_effect=SystemExit))
    live = tmp_path / "job-9"
    live.mkdir()
    (live / "row-0.png").write_bytes(b"old")
    q = _queue(_job(is_reprocess=True))

    with pytest.raises(SystemExit):
        process_one_job(q, infer, tmp_path)

    assert (tmp_path / "job-9.tmp").exists(), "잔여 tmp가 미완 교체의 마커로 남는다"
    assert (live / "row-0.png").read_bytes() == b"old"
    q.requeue_for_reprocess.assert_not_called()
