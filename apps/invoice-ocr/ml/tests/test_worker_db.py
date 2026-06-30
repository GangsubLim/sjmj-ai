"""WorkerQueue 단위 테스트 — MagicMock engine, 라이브 MySQL 불필요."""

from unittest.mock import MagicMock

from worker.db import WorkerQueue

# ---------------------------------------------------------------------------
# mark_done
# ---------------------------------------------------------------------------


def test_mark_done_serializes_json():
    """mark_done은 result_json을 JSON 직렬화해 :r 에 바인딩한다.

    브리프 예시 테스트의 `params["s"] == "done"` 어설션은 실제 SQL과 불일치한다.
    (mark_done SQL: `SET status='done', result_json=:r` — status는 SQL 리터럴, :s 없음)
    실제 params 키는 "r"과 "id"뿐이므로 SQL 문자열과 params["r"]을 검증한다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    q = WorkerQueue(engine)
    q.mark_done(5, {"rows": [], "supply_sum": 0, "warp_ok": True})

    args = conn.execute.call_args
    assert args is not None
    # 첫 번째 위치 인자: text() 객체 (str로 변환 시 SQL 문자열 포함)
    sql_obj = args[0][0]
    assert "status='done'" in str(sql_obj)
    # 두 번째 위치 인자: params dict
    params = args[0][1]
    assert "s" not in params, "status는 SQL 리터럴로 하드코딩 — :s 바인딩 없음"
    assert '"warp_ok"' in params["r"]
    assert params["id"] == 5


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
    {id, image_path} dict 반환.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    # SELECT 결과로 반환될 가짜 행
    fake_row = MagicMock()
    fake_row.id = 42
    fake_row.image_path = "/data/images/invoice_042.jpg"

    # 첫 번째 execute() 호출 → fetchone()이 fake_row 반환
    # 두 번째 execute() 호출 → UPDATE (반환값 미사용)
    first_result = MagicMock()
    first_result.fetchone.return_value = fake_row
    second_result = MagicMock()
    conn.execute.side_effect = [first_result, second_result]

    q = WorkerQueue(engine)
    result = q.claim_next_pending()

    # SELECT + UPDATE = 2회 execute
    assert conn.execute.call_count == 2, "SELECT와 UPDATE 각 1회씩 execute해야 함"

    # 첫 번째 호출 SQL에 FOR UPDATE 포함
    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "FOR UPDATE" in select_sql

    # 두 번째 호출 SQL에 status='running' 포함, id 바인딩
    update_args = conn.execute.call_args_list[1]
    update_sql = str(update_args[0][0])
    assert "status='running'" in update_sql
    update_params = update_args[0][1]
    assert update_params["id"] == 42

    # 반환값 검증
    assert result == {"id": 42, "image_path": "/data/images/invoice_042.jpg"}


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
