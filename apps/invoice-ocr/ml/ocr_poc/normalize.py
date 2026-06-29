"""약식 symbolic 정규화: 천원곱(단가/공급가)·빈칸=0·〃(ditto) 전파. 순수함수.

적용 규칙명을 함께 반환해 리포트가 약식 적용률을 집계할 수 있게 한다(§6)."""
from __future__ import annotations

import re
from dataclasses import dataclass

THOUSAND_THRESHOLD = 1000
_DITTO_MARKS = ("〃", "″", '"', "“", "”")
_THOUSAND_FIELDS = ("unit_price", "amount")


@dataclass(frozen=True)
class NormRow:
    quantity: int | None
    unit_price: int | None
    amount: int | None
    applied: tuple[str, ...]


def normalize_value(raw: str, field: str, prev: int | None) -> tuple[int | None, str | None]:
    """raw(인식 출력) → (정수값, 적용규칙명|None)."""
    text = (raw or "").strip()
    if text in _DITTO_MARKS:
        return prev, "ditto"
    digits = re.sub(r"[^0-9]", "", text)
    if digits == "":
        return 0, "blank_zero"
    value = int(digits)
    if field in _THOUSAND_FIELDS and 0 < value < THOUSAND_THRESHOLD:
        return value * 1000, "thousand_mult"
    return value, None


def normalize_rows(raw_rows: list[dict[str, str]]) -> list[NormRow]:
    """열별로 ditto 전파를 유지하며 행들을 정규화."""
    prev = {"quantity": None, "unit_price": None, "amount": None}
    out: list[NormRow] = []
    for raw in raw_rows:
        applied: list[str] = []
        values: dict[str, int | None] = {}
        for field in ("quantity", "unit_price", "amount"):
            val, rule = normalize_value(raw.get(field, ""), field, prev[field])
            values[field] = val
            if val is not None:
                prev[field] = val
            if rule is not None:
                applied.append(rule)
        out.append(NormRow(values["quantity"], values["unit_price"],
                           values["amount"], tuple(applied)))
    return out
