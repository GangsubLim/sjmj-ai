from ocr_poc.detect import DetectedCell
from ocr_poc.assemble import infer_column_map, assemble_rows, AssembledRow


def test_infer_column_map_by_header_keywords():
    headers = {0: "품목", 1: "수량", 2: "단가", 3: "공급가액"}
    assert infer_column_map(headers) == {"quantity": 1, "unit_price": 2, "amount": 3}


def test_infer_column_map_tolerates_금액_synonym():
    headers = {0: "품 목", 2: "수량", 4: "단 가", 5: "금액"}
    assert infer_column_map(headers) == {"quantity": 2, "unit_price": 4, "amount": 5}


def test_infer_column_map_prefers_supply_over_vat_amount_column():
    headers = {0: "수량", 1: "단가", 2: "부가세금액", 3: "공급가액"}
    assert infer_column_map(headers) == {"quantity": 0, "unit_price": 1, "amount": 3}


def test_assemble_groups_cells_into_rows_by_column_map():
    cmap = {"quantity": 1, "unit_price": 2, "amount": 3}
    cells = [
        DetectedCell(0, 1, (0, 0, 1, 1)), DetectedCell(0, 2, (1, 0, 2, 1)),
        DetectedCell(0, 3, (2, 0, 3, 1)),
        DetectedCell(1, 1, (0, 1, 1, 2)), DetectedCell(1, 3, (2, 1, 3, 2)),  # 단가 누락
    ]
    rows = assemble_rows(cells, cmap)
    assert rows[0] == AssembledRow(0, cells[0], cells[1], cells[2])
    assert rows[1] == AssembledRow(1, cells[3], None, cells[4])


def test_assemble_skips_non_mapped_columns():
    cmap = {"quantity": 1, "unit_price": 2, "amount": 3}
    cells = [DetectedCell(0, 0, (0, 0, 1, 1)),   # 품목 열 → 무시
             DetectedCell(0, 1, (1, 0, 2, 1))]
    rows = assemble_rows(cells, cmap)
    assert rows[0] == AssembledRow(0, cells[1], None, None)
