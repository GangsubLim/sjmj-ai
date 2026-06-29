"""검출 셀을 표 열(수량/단가/공급가)에 매핑해 행별 삼중쌍으로 묶는다.

손라벨을 안 쓰므로 열 식별은 헤더 텍스트 키워드(+ 위치)로 한다. 순수함수.
"""

from __future__ import annotations

from dataclasses import dataclass

from .detect import DetectedCell

_HEADER_KEYWORDS = {
    "quantity": ("수량", "수 량"),
    "unit_price": ("단가", "단 가"),
    "amount": ("공급가액", "공급가", "금액", "공 급 가"),
}


@dataclass(frozen=True)
class AssembledRow:
    row_index: int
    quantity: DetectedCell | None
    unit_price: DetectedCell | None
    amount: DetectedCell | None


def infer_column_map(header_texts: dict[int, str]) -> dict[str, int]:
    """헤더 텍스트(열 인덱스→문자열) → {field: col_index}. 못 찾은 field 는 제외.

    일반 키워드("금액")가 특정 열("부가세금액")에 먼저 걸리지 않도록, field 당
    더 구체적인 키워드를 먼저 시도하고, 이미 점유된 열은 다른 field 가 다시 잡지 않는다.
    """
    norm = {ci: t.replace(" ", "") for ci, t in header_texts.items()}
    out: dict[str, int] = {}
    claimed: set[int] = set()
    for field, keywords in _HEADER_KEYWORDS.items():
        found = None
        for kw in keywords:
            k = kw.replace(" ", "")
            for ci in sorted(norm):
                if ci in claimed:
                    continue
                if k in norm[ci]:
                    found = ci
                    break
            if found is not None:
                break
        if found is not None:
            out[field] = found
            claimed.add(found)
    return out


def assemble_rows(
    cells: list[DetectedCell],
    column_map: dict[str, int],
) -> list[AssembledRow]:
    """매핑된 열의 셀만 행 인덱스로 묶어 삼중쌍 생성."""
    col_to_field = {ci: field for field, ci in column_map.items()}
    by_row: dict[int, dict[str, DetectedCell]] = {}
    for cell in cells:
        field = col_to_field.get(cell.col_index)
        if field is None:
            continue
        by_row.setdefault(cell.row_index, {})[field] = cell
    rows: list[AssembledRow] = []
    for ri in sorted(by_row):
        slot = by_row[ri]
        rows.append(
            AssembledRow(
                row_index=ri,
                quantity=slot.get("quantity"),
                unit_price=slot.get("unit_price"),
                amount=slot.get("amount"),
            )
        )
    return rows
