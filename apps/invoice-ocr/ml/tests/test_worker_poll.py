from unittest.mock import MagicMock

from worker.poll import process_one_job


def test_no_pending_returns_false():
    q = MagicMock()
    q.claim_next_pending.return_value = None
    assert process_one_job(q, lambda *a: {}, "/tmp/crops") is False


def test_done_path_marks_done_with_result():
    q = MagicMock()
    q.claim_next_pending.return_value = {"id": 9, "image_path": "/x.jpg"}
    canned = {"rows": [], "supply_sum": 0, "warp_ok": True}
    assert process_one_job(q, lambda *a: canned, "/tmp/crops") is True
    q.mark_done.assert_called_once_with(9, canned)
    q.mark_failed.assert_not_called()


def test_failure_isolated_marks_failed_not_raised():
    q = MagicMock()
    q.claim_next_pending.return_value = {"id": 3, "image_path": "/x.jpg"}

    def boom(*a):
        raise RuntimeError("warp explode")

    assert process_one_job(q, boom, "/tmp/crops") is True
    q.mark_failed.assert_called_once()
    err = q.mark_failed.call_args[0][1]
    assert "warp explode" in err["error"]
