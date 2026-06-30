"""산술검산(수량×단가=공급가)과 단일 셀 역산 복원. 순수함수.

읽은 supply 를 신뢰 앵커로 두고, quantity/unit_price 중 한쪽만 고쳐
supply 에 맞출 수 있는 유일 후보가 있을 때만 복원안을 제안한다. 검산
결과는 신뢰도 게이트이지 정답 보증이 아니다(§4·§5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationResult:
    """검산 결과(일치 여부·종류·복원안·고친 필드)."""

    consistent: bool
    kind: str
    recovered: tuple[int, int, int] | None
    fixed_field: str | None


def validate_row(qty: int | None, price: int | None, supply: int | None) -> ValidationResult:
    """수량×단가=공급가 검산 후 단일 셀 역산 복원안을 제안한다."""
    if qty is None or price is None or supply is None:
        return ValidationResult(False, "incomplete", None, None)

    if qty * price == supply:
        return ValidationResult(True, "ok", None, None)

    candidates: list[tuple[str, tuple[int, int, int]]] = []
    # quantity 만 고쳐 supply 에 맞추기
    if price != 0 and supply % price == 0:
        candidates.append(("quantity", (supply // price, price, supply)))
    # unit_price 만 고쳐 supply 에 맞추기
    if qty != 0 and supply % qty == 0:
        candidates.append(("unit_price", (qty, supply // qty, supply)))

    unique = {trip: field for field, trip in candidates}
    if len(unique) == 1:
        trip = next(iter(unique))
        return ValidationResult(False, "single_cell_recoverable", trip, unique[trip])
    if len(unique) >= 2:
        return ValidationResult(False, "ambiguous", None, None)
    return ValidationResult(False, "multi_error", None, None)
