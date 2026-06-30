import pytest

from app.core.errors import AppError
from app.core.validators import Validator


def test_required_missing_and_blank():
    v = Validator().required({"a": "", "b": "  ", "c": "ok"}, ["a", "b", "c", "d"])
    assert v.fails()
    assert v.errors["a"] == "a 필드는 필수입니다."
    assert "b" in v.errors and "d" in v.errors and "c" not in v.errors


def test_required_passes_when_present():
    assert Validator().required({"x": "v"}, ["x"]).fails() is False


def test_max_length_multibyte():
    v = Validator().max_length({"recipient": "가" * 101}, "recipient", 100)
    assert v.errors["recipient"] == "recipient은(는) 100자 이하여야 합니다."
    # 정확히 100자는 통과
    assert Validator().max_length({"recipient": "가" * 100}, "recipient", 100).fails() is False


def test_date_format():
    assert Validator().date_format({"issue_date": "2026-05-15"}, "issue_date").fails() is False
    v = Validator().date_format({"issue_date": "2026/05/15"}, "issue_date")
    assert v.errors["issue_date"] == "issue_date은(는) YYYY-MM-DD 형식이어야 합니다."
    # 존재하지 않는 날짜도 거부
    assert Validator().date_format({"issue_date": "2026-13-40"}, "issue_date").fails() is True
    # 미설정 필드는 통과(required가 잡음)
    assert Validator().date_format({}, "issue_date").fails() is False


def test_business_number():
    assert Validator().business_number({"b": "123-45-67890"}, "b").fails() is False  # 10자리
    v = Validator().business_number({"b": "123456789"}, "b")
    assert v.errors["b"] == "사업자번호는 10자리 숫자여야 합니다."
    # 빈값/미설정은 통과
    assert Validator().business_number({"b": ""}, "b").fails() is False


def test_numeric():
    assert Validator().numeric({"n": "100"}, "n").fails() is False
    assert Validator().numeric({"n": "abc"}, "n").errors["n"] == "n은(는) 숫자여야 합니다."


def test_non_empty_array():
    assert Validator().non_empty_array({"items": [1]}, "items").fails() is False
    v = Validator().non_empty_array({"items": []}, "items")
    assert v.errors["items"] == "items은(는) 1개 이상의 항목이 필요합니다."
    assert Validator().non_empty_array({}, "items").fails() is True


def test_chaining_collects_all_errors():
    data = {"recipient": "x"}
    v = Validator().required(data, ["issue_date", "recipient"]).non_empty_array(data, "items")
    assert set(v.errors) == {"issue_date", "items"}


def test_validate_or_fail_raises_structured():
    with pytest.raises(AppError) as ei:
        Validator().required({}, ["x"]).validate_or_fail()
    assert ei.value.status == 400
    assert ei.value.code == "VALIDATION_ERROR"
    assert ei.value.message == "입력값이 올바르지 않습니다."
    assert ei.value.details == {"x": "x 필드는 필수입니다."}


def test_validate_or_fail_noop_when_valid():
    Validator().required({"x": "v"}, ["x"]).validate_or_fail()  # raise 없어야 함
