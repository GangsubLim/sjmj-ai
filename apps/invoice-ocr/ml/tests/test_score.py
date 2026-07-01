from ocr_poc.db import InvoiceItem
from ocr_poc.score import PredRow, aggregate, align_rows, score_invoice


def _item(order, qty, price, supply):
    return InvoiceItem(
        invoice_id=1,
        item_order=order,
        name="x",
        quantity=qty,
        unit_price=price,
        supply=supply,
        vat=supply // 10,
        total=supply + supply // 10,
    )


def test_align_rows_by_amount_value():
    truth = [_item(1, 4, 25000, 100000), _item(2, 1, 30000, 30000)]
    preds = [
        PredRow(1, 30000, 30000, True, True, True),  # 30000 → truth[1]
        PredRow(4, 25000, 100000, True, True, True),
    ]  # 100000 → truth[0]
    aligned = align_rows(preds, truth)
    assert aligned[0][0].amount == 100000 and aligned[0][1].item_order == 1
    assert aligned[1][0].amount == 30000 and aligned[1][1].item_order == 2


def test_score_invoice_counts_three_axes():
    truth = [_item(1, 4, 25000, 100000)]
    preds = [PredRow(4, 25000, 100000, True, True, True)]  # 전부 검출·정답
    s = score_invoice(preds, truth)
    assert s.detect_total == 3 and s.detect_hit == 3
    assert s.recog_total == 3 and s.recog_correct == 3
    assert s.rows_exact == 1 and s.rows_total == 1


def test_score_invoice_undetected_cell_excluded_from_recog():
    truth = [_item(1, 4, 25000, 100000)]
    # unit_price 미검출(detected False) → 검출 리콜 2/3, 인식 모집단 2
    preds = [PredRow(4, None, 100000, True, False, True)]
    s = score_invoice(preds, truth)
    assert s.detect_total == 3 and s.detect_hit == 2
    assert s.recog_total == 2 and s.recog_correct == 2
    assert s.rows_exact == 0  # 행 완전일치 아님(셀 누락)


def test_score_invoice_missing_truth_row_counts_detect_miss():
    truth = [_item(1, 4, 25000, 100000), _item(2, 1, 30000, 30000)]
    preds = [PredRow(4, 25000, 100000, True, True, True)]  # 둘째 행 자체 미검출
    s = score_invoice(preds, truth)
    assert s.detect_total == 6 and s.detect_hit == 3  # 6셀 중 3셀만 검출
    assert s.rows_total == 2 and s.rows_exact == 1


def test_aggregate_micro_ratios():
    from ocr_poc.score import InvoiceScore

    scores = [InvoiceScore(3, 3, 3, 3, 3, 1, 1), InvoiceScore(3, 2, 2, 1, 2, 0, 1)]
    agg = aggregate(scores)
    assert agg["detection_recall"] == 5 / 6
    assert agg["recognition_accuracy"] == 4 / 5
    assert agg["validation_gain_accuracy"] == 5 / 5  # v_cor=3+2=5, r_tot=3+2=5
    assert agg["row_exact_rate"] == 1 / 2
