from ocr_poc.validate import ValidationResult, validate_row


def test_consistent_row():
    r = validate_row(4, 25000, 100000)
    assert r == ValidationResult(True, "ok", None, None)


def test_single_cell_recoverable_unit_price_wrong():
    # qty=4, supply=10000 을 신뢰 → unit_price 를 2500 으로 고치면 일관(유일 복원)
    r = validate_row(4, 25000, 10000)
    assert r.consistent is False
    assert r.kind == "single_cell_recoverable"
    assert r.recovered == (4, 2500, 10000)
    assert r.fixed_field == "unit_price"


def test_ambiguous_when_both_single_fixes_possible():
    # 2*3=6≠12; quantity→12/3=4, unit_price→12/2=6 둘 다 정수 → 모호(잘못된 복원 방지)
    r = validate_row(2, 3, 12)
    assert r.kind == "ambiguous"
    assert r.recovered is None


def test_multi_error_no_single_fix():
    # 7*13=91≠100; 100/13, 100/7 모두 비정수 → 단일복원 불가
    r = validate_row(7, 13, 100)
    assert r.kind == "multi_error"
    assert r.recovered is None


def test_incomplete_when_cell_missing():
    assert validate_row(None, 25000, 100000).kind == "incomplete"
