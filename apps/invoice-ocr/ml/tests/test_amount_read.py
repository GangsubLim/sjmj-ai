import pytest

from handwriting.amount_read import (
    DEGENERATE_BANG_RUN,
    DegenerateOutputError,
    attempt_png_name,
    is_degenerate_raw,
    parse_amount,
    read_amount_with_retry,
)

SPAM = "!" * 32  # 실측 붕괴 출력 — max_tokens=32가 전부 '!'


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


@pytest.mark.parametrize("txt", ["", "   ", "\n"])
def test_parse_amount_empty_output_is_none_not_zero(txt):
    # 빈/공백-only 출력도 퇴화다 — falsy 원문을 0으로 접는 회귀를 막는다
    assert parse_amount(txt) == (None, "")


def _fake_reader(outs):
    """시도별 출력을 순서대로 돌려주고 받은 attempt 인덱스를 기록하는 가짜 판독기."""
    calls = []

    def read_once(attempt):
        calls.append(attempt)
        if attempt >= len(outs):
            # 초과 호출은 SUT 회귀다 — IndexError로 튀면 예외 전파 테스트와 실패 형태가 겹친다
            pytest.fail(
                f"read_once가 준비된 출력({len(outs)}개)보다 많이 호출됨: attempt={attempt}"
            )
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
    assert value is None  # 미판독(None)을 빈칸(0)으로 기록하지 않는다
    assert raw == "!!!→!!!"
    assert len(calls) == 2


def test_empty_raws_are_kept_so_attempt_count_is_recoverable():
    # 빈 원문도 버리지 않는다(amount_read.py의 raws.append 불변식) — 시도 흔적이 raw에 남아야 한다
    read_once, calls = _fake_reader(["", ""])
    assert read_amount_with_retry(read_once) == (None, "→")
    assert calls == [0, 1]


def test_attempts_one_disables_retry():
    read_once, calls = _fake_reader(["!!!", "1500"])
    assert read_amount_with_retry(read_once, attempts=1) == (None, "!!!")
    assert calls == [0]


def test_attempts_three_allows_two_retries():
    read_once, calls = _fake_reader(["!!!", "!!!", "1500"])
    assert read_amount_with_retry(read_once, attempts=3) == (1500, "!!!→!!!→1500")
    assert calls == [0, 1, 2]


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


def test_attempt_png_name_differs_per_attempt_and_idx():
    # 재시도가 직전 시도의 임시 PNG를 덮어쓰지 않게 하는 방어 — 파일명 수준에서 직접 검증
    assert attempt_png_name(3, 0) != attempt_png_name(3, 1)
    names = {attempt_png_name(i, a) for i in range(3) for a in range(2)}
    assert len(names) == 6  # idx×attempt 조합이 전부 서로 다름
    assert attempt_png_name(3, 0).endswith(".png")


# ---------------------------------------------------------------------------
# degenerate 감지 (이슈 #99) — '!' 스팸 한정, 미판독과는 다른 사건이다
# ---------------------------------------------------------------------------


def test_bang_spam_is_degenerate():
    # 실측 붕괴 출력: mlx-vlm generate가 max_tokens까지 '!'만 낸다
    assert is_degenerate_raw(SPAM) is True


def test_bang_run_at_the_threshold_is_degenerate():
    # 경계값을 못 박는다 — 임계 이상이면 True
    assert is_degenerate_raw("!" * DEGENERATE_BANG_RUN) is True


def test_bang_run_below_the_threshold_is_not_degenerate():
    # 기존 테스트가 쓰는 "!!!"(3자) 같은 짧은 느낌표는 degenerate가 아니다 — 무회귀 근거
    assert is_degenerate_raw("!" * (DEGENERATE_BANG_RUN - 1)) is False
    assert is_degenerate_raw("!!!") is False


def test_normal_digits_are_not_degenerate():
    assert is_degenerate_raw("1500") is False
    assert is_degenerate_raw("0") is False


@pytest.mark.parametrize("raw", ["—" * 32, "-" * 32, "", "   "])
def test_dash_fill_and_blank_output_are_not_degenerate(raw):
    # '반복 문자 일반'으로 일반화하면 대시 채움·빈칸 같은 정상 미판독이 전부 붕괴로 오판된다.
    # 감지는 '!' 한정이어야 한다(spec §1).
    assert is_degenerate_raw(raw) is False


def test_digits_mixed_with_spam_are_degenerate():
    # 스팸 앞뒤에 숫자가 섞여도 붕괴다 — 값이 파싱된다고 정상으로 접으면 안 된다
    assert is_degenerate_raw("1500" + SPAM) is True
    assert is_degenerate_raw(SPAM + "1500") is True


def test_spam_raises_immediately_without_retrying():
    # sticky 붕괴라 재시도는 죽은 프로세스에 던지는 낭비 호출일 뿐이다(spec §1).
    read_once, calls = _fake_reader([SPAM, "1500"])

    with pytest.raises(DegenerateOutputError):
        read_amount_with_retry(read_once)

    assert calls == [0], "스팸을 보면 그 자리에서 멈춘다"


def test_spam_on_the_second_attempt_also_raises():
    read_once, calls = _fake_reader(["—", SPAM])

    with pytest.raises(DegenerateOutputError):
        read_amount_with_retry(read_once)

    assert calls == [0, 1]


def test_degenerate_error_carries_a_raw_sample():
    # 워커 로그가 이 메시지를 그대로 찍는다 — 표본이 없으면 사후 판별이 불가능하다
    read_once, _ = _fake_reader(["!" * 200])

    with pytest.raises(DegenerateOutputError) as exc:
        read_amount_with_retry(read_once)

    assert "!" in str(exc.value)
    assert len(str(exc.value)) < 200, "raw 표본은 DEGENERATE_RAW_SAMPLE 근처로 잘린다"


def test_non_spam_unreadable_output_still_retries_and_returns_none():
    # 계약 불변식: 미판독과 degenerate는 다른 사건이다. 대시·빈칸은 지금처럼 재시도 후 (None, raw).
    read_once, calls = _fake_reader(["—", "—"])

    assert read_amount_with_retry(read_once) == (None, "—→—")
    assert calls == [0, 1]
