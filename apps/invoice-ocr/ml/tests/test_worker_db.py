"""WorkerQueue 단위 테스트 — MagicMock engine, 라이브 MySQL 불필요."""

from unittest.mock import MagicMock

from handwriting.relink import RELINK_FAILED, NewRow, OldPair, plan_relink
from worker.db import WorkerQueue

# ---------------------------------------------------------------------------
# commit_job — 초안 갱신 + 2-pass 승계를 한 트랜잭션으로
# ---------------------------------------------------------------------------


def _executed(conn):
    """conn.execute 호출을 (sql, params) 목록으로 편다."""
    return [(str(c[0][0]), c[0][1] if len(c[0]) > 1 else {}) for c in conn.execute.call_args_list]


def test_commit_job_updates_draft_and_marks_done():
    """신규 잡(빈 계획)은 ① 초안 갱신 + done 전이만 수행한다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [], [NewRow(row_index=0, supply=3000)])

    WorkerQueue(engine).commit_job(5, {"rows": [], "supply_sum": 0, "warp_ok": True}, plan)

    calls = _executed(conn)
    assert len(calls) == 1, "빈 계획이면 training_pairs를 건드리지 않는다"
    sql, params = calls[0]
    assert "status='done'" in sql
    assert '"warp_ok"' in params["r"]
    assert params["id"] == 5


def test_commit_job_uses_a_single_transaction():
    """초안 갱신과 승계가 갈라지면 그 사이가 정식 중간 단계로 승격된다(ADR 0010)."""
    engine = MagicMock()
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert engine.begin.call_count == 1


def test_commit_job_locks_parent_before_children():
    """락 순서는 잡(부모) → 쌍(자식) — 사람의 PATCH와 순환 대기에 걸리지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    tables = ["ocr_jobs" if "ocr_jobs" in sql else "training_pairs" for sql, _ in _executed(conn)]
    assert tables[0] == "ocr_jobs"
    assert "ocr_jobs" not in tables[1:]


def test_commit_job_empties_row_namespace_before_writing_final_refs():
    """1단계가 그 잡의 쌍 전량을 row- 밖으로 뺀 뒤에야 2단계가 최종 좌표를 쓴다(§5).

    전부 한 칸씩 밀리는 케이스 — 순차 UPDATE였다면 첫 문장부터 duplicate key로 죽는다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    olds = [OldPair(1, 0, 3000), OldPair(2, 1, 5000)]
    plan = plan_relink(5, olds, [NewRow(0, 9000), NewRow(1, 3000), NewRow(2, 5000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    refs = [params.get("ref") for _, params in _executed(conn)[1:]]
    assert refs == ["job-5/tmp-1", "job-5/tmp-2", "job-5/row-1", "job-5/row-2"]


def test_commit_job_moves_orphans_out_in_stage_one():
    """미결 전환이 1단계에 함께 들어가야 한다 — 뒤로 미루면 옛 row-N을 점유한 채 남는다.

    옛 row-0이 미결이고 옛 row-1이 row-0으로 승계되는 경우가 정확히 그 충돌이다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    olds = [OldPair(1, 0, 3000), OldPair(2, 1, 5000)]
    plan = plan_relink(5, olds, [NewRow(0, 5000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    calls = _executed(conn)[1:]
    stage_one_refs = [p["ref"] for _, p in calls[:2]]
    assert set(stage_one_refs) == {"job-5/orphan-1", "job-5/tmp-2"}
    assert calls[2][1]["ref"] == "job-5/row-0"


def test_commit_job_marks_orphans_excluded_with_reason_and_clears_review():
    """미결 쌍은 excluded + relink_failed + reviewed_at NULL로 사람 큐에 오른다(§6·§7)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[1]
    assert "status='excluded'" in sql
    assert "reviewed_at=NULL" in sql
    assert params["reason"] == RELINK_FAILED


def test_commit_job_restores_machine_excluded_pairs_when_relink_succeeds():
    """지난 재처리가 미결로 배제한 쌍이 이번에 승계되면 배제를 자동 해제한다.

    반복 재처리가 이 기능의 전제라, 여기서 되돌리지 않으면 그림이 돌아온 쌍이
    excluded·relink_failed인 채 남아 뱅크에 영영 들어가지 않는다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[-1]
    assert "status = CASE WHEN exclusion_reason=:reason THEN 'included' ELSE status END" in sql
    assert (
        "exclusion_reason = CASE WHEN exclusion_reason=:reason THEN NULL ELSE exclusion_reason END"
        in sql
    )
    assert params["reason"] == RELINK_FAILED


def test_commit_job_leaves_human_excluded_pairs_excluded_when_relink_succeeds():
    """사람이 배제한 쌍은 승계돼도 배제로 남는다 — 복원 조건이 기계 사유에만 걸려 있다.

    사람이 배제하면 backend의 curation_repository.update_pair가 같은 UPDATE에서
    exclusion_reason을 NULL로 지운다(ADR 0006). 사유가 남아 있다는 것 자체가 "아직 기계
    판정이며 사람이 손대지 않았다"의 표식이라, 조건을 사유에 걸면 사람 소유 배제는
    자동으로 제외된다. 무조건 복원으로 바뀌면 이 단언이 RED가 된다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[-1]
    assert sql.count("CASE WHEN exclusion_reason=:reason") == 2, "복원 조건은 기계 사유에만 건다"
    assert params["reason"] == RELINK_FAILED
    assert "status='included'" not in sql, "무조건 복원이면 사람 배제까지 되돌아온다"
    assert "exclusion_reason=NULL" not in sql, "사람 소유 표식(NULL 사유)을 덮어쓰지 않는다"


def test_commit_job_keeps_reviewed_at_of_relinked_pairs():
    """승계 성공 쌍의 reviewed_at은 그대로 둔다 — 사람이 볼 것이 미결뿐이 되도록(§7)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert all("reviewed_at" not in sql for sql, _ in _executed(conn))


def test_commit_job_releases_gate_only_when_orphans_exist():
    """미결이 나온 잡만 게이트를 해제한다(ADR 0011). curation_reviewed_at은 지우지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    job_sql = _executed(conn)[0][0]
    assert "curation_reviewed = 0" in job_sql
    assert "curation_reviewed_at" not in job_sql


def test_commit_job_keeps_gate_when_every_pair_is_relinked():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert "curation_reviewed" not in _executed(conn)[0][0]


def test_commit_job_does_not_touch_draft_label():
    """correction_json.lines[].draft_label과의 짝을 깨지 않는다(§8)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert all("draft_label" not in sql for sql, _ in _executed(conn))


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


def test_mark_failed_serializes_json():
    """mark_failed는 error_json을 JSON 직렬화해 :r 에 바인딩한다.
    SQL에 status='failed' 리터럴, :s 바인딩 없음.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    q = WorkerQueue(engine)
    q.mark_failed(7, {"code": "TIMEOUT", "msg": "OCR timed out"})

    args = conn.execute.call_args
    assert args is not None
    sql_obj = args[0][0]
    assert "status='failed'" in str(sql_obj)
    params = args[0][1]
    assert "s" not in params, "status는 SQL 리터럴로 하드코딩 — :s 바인딩 없음"
    assert '"TIMEOUT"' in params["r"]
    assert params["id"] == 7


# ---------------------------------------------------------------------------
# claim_next_pending — row 존재
# ---------------------------------------------------------------------------


def test_claim_next_pending_transitions_and_returns():
    """pending 행이 있으면 SELECT(FOR UPDATE) → UPDATE(running) 순서로 2회 execute,
    {id, image_path, is_reprocess} dict 반환.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    fake_row = MagicMock()
    fake_row.id = 42
    fake_row.image_path = "/data/images/invoice_042.jpg"
    fake_row.is_reprocess = 0

    first_result = MagicMock()
    first_result.fetchone.return_value = fake_row
    second_result = MagicMock()
    conn.execute.side_effect = [first_result, second_result]

    q = WorkerQueue(engine)
    result = q.claim_next_pending()

    assert conn.execute.call_count == 2, "SELECT와 UPDATE 각 1회씩 execute해야 함"
    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "FOR UPDATE" in select_sql
    update_args = conn.execute.call_args_list[1]
    assert "status='running'" in str(update_args[0][0])
    assert update_args[0][1]["id"] == 42
    assert result == {
        "id": 42,
        "image_path": "/data/images/invoice_042.jpg",
        "is_reprocess": False,
    }


def test_claim_next_pending_orders_new_uploads_before_reprocessing():
    """재처리 잡은 정의상 옛 id라 순번만으로 세우면 신규 업로드를 밀어낸다(spec §2)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    WorkerQueue(engine).claim_next_pending()

    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "ORDER BY (result_json IS NOT NULL), id" in select_sql


def test_claim_next_pending_flags_reprocessing_jobs():
    """pending인데 result_json이 이미 있으면 재처리다 — 표식 컬럼을 만들지 않는다(§1)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    fake_row = MagicMock()
    fake_row.id = 7
    fake_row.image_path = "/x.jpg"
    fake_row.is_reprocess = 1
    first = MagicMock()
    first.fetchone.return_value = fake_row
    conn.execute.side_effect = [first, MagicMock()]

    assert WorkerQueue(engine).claim_next_pending()["is_reprocess"] is True


def test_rollback_to_done_preserves_result_json():
    """재처리 실패는 failed가 아니라 done으로 되돌린다 — 옛 초안·옛 크롭이 그대로 정합이다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    WorkerQueue(engine).rollback_to_done(11)

    sql = str(conn.execute.call_args[0][0])
    assert "status='done'" in sql
    assert "result_json" not in sql, "옛 초안을 덮으면 되돌릴 대상이 사라진다"
    assert conn.execute.call_args[0][1]["id"] == 11


def test_requeue_for_reprocess_sets_pending_without_touching_result_json():
    """커밋 후 교체 실패의 복구 경로 — result_json이 남아야 다시 재처리로 판별된다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    WorkerQueue(engine).requeue_for_reprocess(11)

    sql = str(conn.execute.call_args[0][0])
    assert "status='pending'" in sql
    assert "result_json" not in sql


# ---------------------------------------------------------------------------
# claim_next_pending — 행 없음
# ---------------------------------------------------------------------------


def test_claim_next_pending_returns_none_when_empty():
    """pending 행이 없으면 None 반환, UPDATE를 실행하지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    q = WorkerQueue(engine)
    result = q.claim_next_pending()

    assert result is None
    # SELECT만 1회, UPDATE 없음
    assert conn.execute.call_count == 1, "빈 큐면 SELECT만 실행, UPDATE 없어야 함"


# ---------------------------------------------------------------------------
# fetch_pairs
# ---------------------------------------------------------------------------


def test_fetch_pairs_returns_anchor_inputs_in_row_order():
    """승계 입력은 (pair_id, row_index, supply) 셋뿐 — 라벨은 승계가 건드리지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchall.return_value = [(1, 0, 3000), (2, 1, None)]

    pairs = WorkerQueue(engine).fetch_pairs(9)

    assert pairs == [OldPair(1, 0, 3000), OldPair(2, 1, None)]
    sql = str(conn.execute.call_args[0][0])
    assert "ORDER BY row_index" in sql
    assert conn.execute.call_args[0][1]["id"] == 9
