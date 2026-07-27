import pytest

from handwriting.amount_read import parse_amount, read_amount_with_retry


def test_parse_amount_pure_digits():
    assert parse_amount("1500") == (1500, "1500")


def test_parse_amount_joins_digits_in_mixed_text():
    # 콤마·단위가 섞여도 숫자만 연결한다(기존 read_amount 파싱과 동일 동작)
    assert parse_amount("1,500원") == (1500, "1,500원")


def test_parse_amount_returns_none_when_no_digits():
    # 퇴화 출력은 값 None + 원문 보존 — 0(빈칸)으로 강등하지 않는다
    value, raw = parse_amount(" !!! ")
    assert value is None
    assert raw == "!!!"


def _fake_reader(outs):
    """시도별 출력을 순서대로 돌려주고 받은 attempt 인덱스를 기록하는 가짜 판독기."""
    calls = []

    def read_once(attempt):
        calls.append(attempt)
        return outs[attempt]

    return read_once, calls


def test_first_attempt_success_does_not_retry():
    # 정상 판독 경로 회귀 없음 — 호출 1회, raw 형식도 기존 그대로
    read_once, calls = _fake_reader(["1500"])
    assert read_amount_with_retry(read_once) == (1500, "1500")
    assert calls == [0]


def test_degenerate_first_attempt_retries_and_succeeds():
    read_once, calls = _fake_reader(["!!!", "1500"])
    assert read_amount_with_retry(read_once) == (1500, "!!!→1500")
    assert len(calls) == 2


def test_all_attempts_degenerate_keeps_none_not_zero():
    read_once, calls = _fake_reader(["!!!", "!!!"])
    value, raw = read_amount_with_retry(read_once)
    assert value is None
    assert value != 0  # 미판독(None)을 빈칸(0)으로 기록하지 않는다
    assert raw == "!!!→!!!"
    assert len(calls) == 2


def test_zero_reading_is_valid_not_degenerate():
    # AMT_PROMPT가 빈칸에 "0"을 지시(infer_photo.py:75)하므로 "0"은 흔한 정상 판독이다.
    # 퇴화(숫자 0개)와 혼동하면 전 전표 빈칸이 재시도+None 강등된다 — 이슈 #20 불변식의 역방향.
    read_once, calls = _fake_reader(["0", "1500"])
    assert read_amount_with_retry(read_once) == (0, "0")
    assert calls == [0]


def test_attempt_index_increments_per_attempt():
    # 호출측이 attempt별 고유 임시파일 경로를 만들 수 있게 하는 계약
    read_once, calls = _fake_reader(["!!!", "!!!"])
    read_amount_with_retry(read_once)
    assert calls == [0, 1]


def test_read_once_exception_propagates_and_does_not_retry():
    # design.md 비목표: generate 예외는 기존대로 전파(재시도 트리거가 아니다).
    calls = []

    def read_once(attempt):
        calls.append(attempt)
        raise RuntimeError("generate failed")

    with pytest.raises(RuntimeError):
        read_amount_with_retry(read_once)
    assert calls == [0]
