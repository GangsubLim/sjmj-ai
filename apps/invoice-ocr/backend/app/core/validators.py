"""Validator — fluent 입력 검증기."""

import re
from datetime import datetime

from .errors import bad_request


class Validator:
    """입력값을 fluent 체인으로 검증하는 검증기."""

    def __init__(self) -> None:
        """빈 에러 맵으로 검증기를 초기화한다."""
        self.errors: dict[str, str] = {}

    def required(self, data: dict, fields: list[str]) -> "Validator":
        """지정한 필드들이 비어 있지 않은지 검증한다."""
        for f in fields:
            v = data.get(f)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                self.errors[f] = f"{f} 필드는 필수입니다."
        return self

    def max_length(self, data: dict, field: str, max_: int) -> "Validator":
        """필드 값의 길이가 max_ 이하인지 검증한다."""
        v = data.get(field)
        if v is not None and len(str(v)) > max_:
            self.errors[field] = f"{field}은(는) {max_}자 이하여야 합니다."
        return self

    def date_format(self, data: dict, field: str) -> "Validator":
        """필드 값이 YYYY-MM-DD 형식인지 검증한다."""
        v = data.get(field)
        if v is not None:
            try:
                parsed = datetime.strptime(str(v), "%Y-%m-%d")
                ok = parsed.strftime("%Y-%m-%d") == str(v)
            except ValueError:
                ok = False
            if not ok:
                self.errors[field] = f"{field}은(는) YYYY-MM-DD 형식이어야 합니다."
        return self

    def business_number(self, data: dict, field: str) -> "Validator":
        """필드 값이 숫자 10자리 사업자번호인지 검증한다."""
        v = data.get(field)
        if v is not None and v != "":
            digits = re.sub(r"[^0-9]", "", str(v))
            if len(digits) != 10:
                self.errors[field] = "사업자번호는 10자리 숫자여야 합니다."
        return self

    def numeric(self, data: dict, field: str) -> "Validator":
        """필드 값이 숫자로 변환 가능한지 검증한다."""
        v = data.get(field)
        if v is not None:
            try:
                float(v)
            except (TypeError, ValueError):
                self.errors[field] = f"{field}은(는) 숫자여야 합니다."
        return self

    def non_empty_array(self, data: dict, field: str) -> "Validator":
        """필드 값이 1개 이상의 항목을 가진 리스트인지 검증한다."""
        v = data.get(field)
        if not isinstance(v, list) or len(v) == 0:
            self.errors[field] = f"{field}은(는) 1개 이상의 항목이 필요합니다."
        return self

    def fails(self) -> bool:
        """검증 에러가 하나라도 있으면 True를 반환한다."""
        return bool(self.errors)

    def validate_or_fail(self) -> None:
        """검증 실패 시 누적된 에러로 bad_request를 발생시킨다."""
        if self.fails():
            bad_request("입력값이 올바르지 않습니다.", self.errors)
