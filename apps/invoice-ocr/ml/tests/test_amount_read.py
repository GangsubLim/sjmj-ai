from handwriting.amount_read import parse_amount


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
