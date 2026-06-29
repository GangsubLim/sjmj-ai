"""DB/라벨 정답 대비 3축 채점: 검출 리콜 / 인식 정확도(검출 한정) / 검산 게인.

행정렬은 손라벨 순서를 신뢰하지 않고 amount(=supply) 값으로 매칭한다(§5).
모두 순수함수.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import InvoiceItem

_FIELDS = ("quantity", "unit_price", "amount")


@dataclass(frozen=True)
class PredRow:
    """예측 한 행(수량·단가·공급가 값과 필드별 검출 여부)."""

    quantity: int | None
    unit_price: int | None
    amount: int | None
    detected_quantity: bool
    detected_unit_price: bool
    detected_amount: bool


@dataclass(frozen=True)
class InvoiceScore:
    """한 invoice의 3축 채점 집계 카운트."""

    detect_total: int
    detect_hit: int
    recog_total: int
    recog_correct: int
    valgain_correct: int
    rows_exact: int
    rows_total: int


def align_rows(
    preds: list[PredRow],
    truth: list[InvoiceItem],
) -> list[tuple[PredRow | None, InvoiceItem]]:
    """정답행마다 amount 값이 같은 예측행을 1:1 매칭. 없으면 None."""
    remaining = list(preds)
    aligned: list[tuple[PredRow | None, InvoiceItem]] = []
    for t in truth:
        match = None
        for p in remaining:
            if p.amount == t.supply:
                match = p
                break
        if match is not None:
            remaining.remove(match)
        aligned.append((match, t))
    return aligned


def _pred_field(p: PredRow, field: str) -> int | None:
    return getattr(p, field)


def _pred_detected(p: PredRow, field: str) -> bool:
    return getattr(p, f"detected_{field}")


def _truth_field(t: InvoiceItem, field: str) -> int:
    return (
        t.quantity if field == "quantity" else (t.unit_price if field == "unit_price" else t.supply)
    )


def score_invoice(preds: list[PredRow], truth: list[InvoiceItem]) -> InvoiceScore:
    """예측행을 정답행에 정렬해 invoice 단위 3축 카운트를 집계한다."""
    aligned = align_rows(preds, truth)
    detect_total = detect_hit = 0
    recog_total = recog_correct = valgain_correct = 0
    rows_exact = 0
    for pred, t in aligned:
        row_correct = True
        for field in _FIELDS:
            detect_total += 1
            detected = pred is not None and _pred_detected(pred, field)
            if not detected:
                detect_hit += 0
                row_correct = False
                continue
            detect_hit += 1
            recog_total += 1
            correct = _pred_field(pred, field) == _truth_field(t, field)
            recog_correct += int(correct)
            valgain_correct += int(correct)
            if not correct:
                row_correct = False
        rows_exact += int(row_correct)
    return InvoiceScore(
        detect_total=detect_total,
        detect_hit=detect_hit,
        recog_total=recog_total,
        recog_correct=recog_correct,
        valgain_correct=valgain_correct,
        rows_exact=rows_exact,
        rows_total=len(truth),
    )


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def aggregate(scores: list[InvoiceScore]) -> dict[str, float]:
    """invoice별 점수를 합산해 전체 3축 비율 지표로 환산한다."""
    d_tot = sum(s.detect_total for s in scores)
    d_hit = sum(s.detect_hit for s in scores)
    r_tot = sum(s.recog_total for s in scores)
    r_cor = sum(s.recog_correct for s in scores)
    v_cor = sum(s.valgain_correct for s in scores)
    rows_tot = sum(s.rows_total for s in scores)
    rows_exact = sum(s.rows_exact for s in scores)
    return {
        "detection_recall": _ratio(d_hit, d_tot),
        "recognition_accuracy": _ratio(r_cor, r_tot),
        "validation_gain_accuracy": _ratio(v_cor, r_tot),
        "row_exact_rate": _ratio(rows_exact, rows_tot),
    }
