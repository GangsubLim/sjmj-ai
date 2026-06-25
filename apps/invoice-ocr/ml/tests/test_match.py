from pathlib import Path

from ocr_poc.data import LabelRow
from ocr_poc.db import parse_backup
from ocr_poc.match import (
    extract_date, total_supply_from_labels, build_review_rows,
    write_review_csv, read_review_csv, resolve_ground_truth, ReviewRow,
)


def test_extract_date_various_formats():
    assert extract_date(["합계", "발행일 2026-05-12", "옥천운수"]) == "2026-05-12"
    assert extract_date(["2026년 05월 13일", "x"]) == "2026-05-13"
    assert extract_date(["2026.05.14"]) == "2026-05-14"
    assert extract_date(["날짜없음"]) is None


def test_total_supply_from_labels_sums_amount():
    rows = [LabelRow(1, "a", "4", "25000", "100000"),
            LabelRow(2, "b", "1", "30000", "30000")]
    assert total_supply_from_labels(rows) == 130000


def test_build_review_rows_marks_status(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    per_image = [
        ("inv_a", ["2026-05-12"], 300000),   # 일자+공급가합 → 유일(inv 11)
        ("inv_b", ["2026-05-12"], 999999),   # 공급가합 불일치 → 0건
    ]
    rows = build_review_rows(per_image, db)
    by_id = {r.image_id: r for r in rows}
    assert by_id["inv_a"].db_match_count == 1
    assert by_id["inv_a"].status == "unique"
    assert by_id["inv_b"].db_match_count == 0
    assert by_id["inv_b"].status == "no_match"


def test_review_csv_roundtrip(tmp_path: Path):
    rows = [ReviewRow("inv_a", "2026-05-12", 300000, 1, "unique")]
    p = tmp_path / "reviewed_dates.csv"
    write_review_csv(rows, p)
    assert read_review_csv(p) == rows


def test_resolve_ground_truth_unique_only(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    reviewed = [
        ReviewRow("inv_a", "2026-05-12", 300000, 1, "unique"),
        ReviewRow("inv_b", "2026-05-12", 999999, 0, "no_match"),
    ]
    gt = resolve_ground_truth(reviewed, db)
    assert set(gt) == {"inv_a"}
    assert gt["inv_a"].invoice_id == 11
    assert gt["inv_a"].rows[0].supply == 300000


def test_resolve_ground_truth_date_none_falls_back_to_total_supply(tiny_invoices_sql):
    # 날짜 추출 실패(None)면 total_supply 단독 유일조회로 폴백 — 유일할 때만 채택
    db = parse_backup(tiny_invoices_sql)
    reviewed = [
        ReviewRow("inv_c", None, 120000, 1, "unique"),    # 공급가합 유일(inv 12) → 채택
        ReviewRow("inv_d", None, 999999, 0, "no_match"),  # 공급가합 0건 → 미채택
    ]
    gt = resolve_ground_truth(reviewed, db)
    assert set(gt) == {"inv_c"}
    assert gt["inv_c"].invoice_id == 12


def test_review_csv_roundtrip_with_none_date(tmp_path: Path):
    # extracted_date=None ↔ "" 변환이 왕복에서 보존되는지
    rows = [ReviewRow("inv_x", None, 0, 0, "no_match")]
    p = tmp_path / "reviewed_dates.csv"
    write_review_csv(rows, p)
    assert read_review_csv(p) == rows


def test_label_sum_resolves_ground_truth_against_db(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    labels = [LabelRow(1, "단지", "1", "300000", "300000")]   # inv 11: 공급가 300000
    ts = total_supply_from_labels(labels)
    assert ts == 300000
    per_image = [("inv_11", ["2026-05-12"], ts)]
    rows = build_review_rows(per_image, db)
    assert rows[0].db_match_count == 1 and rows[0].status == "unique"
    gt = resolve_ground_truth(rows, db)
    assert set(gt) == {"inv_11"} and gt["inv_11"].invoice_id == 11
