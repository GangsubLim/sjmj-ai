"""38장 배치 오케스트레이션 CLI.

서브커맨드:
  match-extract : references OCR → reviewed_dates.csv (검수 전 단계)
  run           : 검수된 CSV + DB → 38장 배치 추론 → report/

run_pipeline 는 어댑터/이미지오프너를 주입받는 순수 오케스트레이션이라
모킹으로 end-to-end 스모크가 가능하다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import config, report, score
from .assemble import assemble_rows, infer_column_map
from .crop import crop_cell
from .data import Sample, load_samples
from .detect import DetectorAdapter
from .match import GroundTruth
from .normalize import normalize_rows
from .recognize import RecognizerAdapter

_RESULTS = Path("results")
_REPORT = Path("report")


def _recognize_cell(image, cell, recognizer: RecognizerAdapter) -> str:
    crop = crop_cell(image, cell.bbox)
    if crop is None:
        return ""
    return recognizer.recognize(crop)


def _positional_column_map(cells) -> dict[str, int]:
    """헤더 인식 실패 시 폴백: 열별 x-중심으로 오른쪽부터 amount/unit_price/quantity.

    고정 영수증 템플릿(…|수량|단가|공급가)에서 공급가(amount)가 항상 최우측이라
    기하만으로 매핑한다. 인식에 의존하지 않는다.
    """
    from statistics import median  # noqa: PLC0415

    by_col: dict[int, list[float]] = {}
    for c in cells:
        by_col.setdefault(c.col_index, []).append((c.bbox[0] + c.bbox[2]) / 2)
    if not by_col:
        return {}
    left_to_right = sorted(by_col, key=lambda ci: median(by_col[ci]))
    mapping: dict[str, int] = {}
    for field, ci in zip(("amount", "unit_price", "quantity"), reversed(left_to_right)):
        mapping[field] = ci
    return mapping


def run_pipeline(
    samples: list[Sample],
    ground_truth: dict[str, GroundTruth],
    detector: DetectorAdapter,
    recognizer: RecognizerAdapter,
    image_opener,
) -> tuple[list[score.InvoiceScore], report.ReportData]:
    invoice_scores: list[score.InvoiceScore] = []
    per_image: list[dict] = []
    failures: list[dict] = []
    rule_counts: dict[str, int] = {}

    for sample in samples:
        gt = ground_truth.get(sample.image_id)
        if gt is None:
            failures.append({"image_id": sample.image_id, "reason": "no_ground_truth"})
            continue
        try:
            image = image_opener(sample.image_path)
            cells = detector.detect(str(sample.image_path))
            if not cells:
                failures.append({"image_id": sample.image_id, "reason": "no_table_detected"})
                continue

            header_row = min(c.row_index for c in cells)
            header_texts = {
                c.col_index: _recognize_cell(image, c, recognizer)
                for c in cells
                if c.row_index == header_row
            }
            column_map = infer_column_map(header_texts)
            used_header = "amount" in column_map
            if not used_header:
                # 손글씨 양식: 헤더 키워드 인식 실패 → 고정 템플릿 위치기반 폴백
                # (오른쪽부터 공급가/단가/수량). 셀 위치화는 텍스트검출, 열은 기하.
                column_map = _positional_column_map(cells)
            if "amount" not in column_map:
                failures.append({"image_id": sample.image_id, "reason": "column_map_failed"})
                continue

            data_cells = [c for c in cells if c.row_index != header_row] if used_header else cells
            arows = assemble_rows(data_cells, column_map)

            raw_rows: list[dict[str, str]] = []
            detected_flags: list[dict[str, bool]] = []
            for ar in arows:
                raw = {}
                det = {}
                for field, cell in (
                    ("quantity", ar.quantity),
                    ("unit_price", ar.unit_price),
                    ("amount", ar.amount),
                ):
                    if cell is None:
                        raw[field] = ""
                        det[field] = False
                    else:
                        raw[field] = _recognize_cell(image, cell, recognizer)
                        det[field] = True
                raw_rows.append(raw)
                detected_flags.append(det)

            norm = normalize_rows(raw_rows)
            for nr in norm:
                for rule in nr.applied:
                    rule_counts[rule] = rule_counts.get(rule, 0) + 1

            preds = [
                score.PredRow(
                    quantity=n.quantity,
                    unit_price=n.unit_price,
                    amount=n.amount,
                    detected_quantity=d["quantity"],
                    detected_unit_price=d["unit_price"],
                    detected_amount=d["amount"],
                )
                for n, d in zip(norm, detected_flags)
            ]
            s = score.score_invoice(preds, list(gt.rows))
            invoice_scores.append(s)
            per_image.append(
                {"image_id": sample.image_id, "rows": s.rows_total, "rows_exact": s.rows_exact}
            )
        except Exception as exc:  # 개별 이미지 실패 격리(§6)
            failures.append({"image_id": sample.image_id, "reason": f"error: {exc}"})

    metrics = score.aggregate(invoice_scores)
    data = report.ReportData(
        metrics=metrics, per_image=per_image, failures=failures, rule_counts=rule_counts
    )
    return invoice_scores, data


def _cmd_run() -> None:
    from PIL import Image

    from .db import parse_backup
    from .detect import TextDetCellDetector
    from .match import read_review_csv, resolve_ground_truth
    from .recognize import PaddleOCRNumeric

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    reviewed = read_review_csv(_RESULTS / "reviewed_dates.csv")
    gt = resolve_ground_truth(reviewed, db)
    samples = load_samples(config.images_dir(), config.labels_dir())
    _, data = run_pipeline(samples, gt, TextDetCellDetector(), PaddleOCRNumeric(), Image.open)
    report.write_report(data, _REPORT)
    print(f"[run] report → {_REPORT / 'sp1-report.md'}  (정답지 {len(gt)}/{len(samples)})")


def _cmd_match_extract() -> None:
    """References 전체 OCR → 발행일+공급가합 → reviewed_dates.csv. 라벨 미사용(검수 전)."""
    from .db import parse_backup
    from .match import build_review_rows_from_references, write_review_csv
    from .recognize import ReferenceOCR

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    samples = load_samples(config.images_dir(), config.labels_dir())
    ocr = ReferenceOCR()
    per_image: list[tuple[str, list[str]]] = []
    for sample in samples:
        ref_path = config.references_dir() / f"{sample.image_id}.jpg"
        texts = ocr.texts(str(ref_path)) if ref_path.is_file() else []
        per_image.append((sample.image_id, texts))
    rows = build_review_rows_from_references(per_image, db)
    write_review_csv(rows, _RESULTS / "reviewed_dates.csv")
    resolved = sum(1 for r in rows if r.status == "unique")
    print(
        f"[match-extract] reviewed_dates.csv → {_RESULTS}  ({len(rows)}행, "
        f"unique {resolved}) — 검수 후 run"
    )


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "match-extract":
        _cmd_match_extract()
    elif cmd == "run":
        _cmd_run()
    else:
        print("usage: python -m ocr_poc [match-extract|run]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
