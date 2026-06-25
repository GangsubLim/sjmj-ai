from ocr_poc.normalize import normalize_value, normalize_rows, NormRow


def test_quantity_is_literal_no_thousand_mult():
    assert normalize_value("4", "quantity", None) == (4, None)


def test_unit_price_thousand_mult_below_threshold():
    assert normalize_value("25", "unit_price", None) == (25000, "thousand_mult")
    assert normalize_value("365000", "unit_price", None) == (365000, None)


def test_amount_thousand_mult():
    assert normalize_value("100", "amount", None) == (100000, "thousand_mult")


def test_blank_is_zero():
    assert normalize_value("", "amount", None) == (0, "blank_zero")


def test_ditto_propagates_prev():
    assert normalize_value("〃", "unit_price", 25000) == (25000, "ditto")
    assert normalize_value("″", "unit_price", 30000) == (30000, "ditto")


def test_normalize_rows_propagates_ditto_down_column():
    raw_rows = [
        {"quantity": "4", "unit_price": "25", "amount": "100"},
        {"quantity": "1", "unit_price": "〃", "amount": "25"},
    ]
    rows = normalize_rows(raw_rows)
    assert rows[0] == NormRow(4, 25000, 100000, ("thousand_mult", "thousand_mult"))
    assert rows[1].unit_price == 25000   # 윗행 단가 전파
    assert "ditto" in rows[1].applied
