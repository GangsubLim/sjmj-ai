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
                for c in cells if c.row_index == header_row
            }
            column_map = infer_column_map(header_texts)
            if "amount" not in column_map:
                failures.append({"image_id": sample.image_id, "reason": "column_map_failed"})
                continue

            data_cells = [c for c in cells if c.row_index != header_row]
            arows = assemble_rows(data_cells, column_map)

            raw_rows: list[dict[str, str]] = []
            detected_flags: list[dict[str, bool]] = []
            for ar in arows:
                raw = {}
                det = {}
                for field, cell in (("quantity", ar.quantity),
                                    ("unit_price", ar.unit_price),
                                    ("amount", ar.amount)):
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
                    quantity=n.quantity, unit_price=n.unit_price, amount=n.amount,
                    detected_quantity=d["quantity"], detected_unit_price=d["unit_price"],
                    detected_amount=d["amount"],
                )
                for n, d in zip(norm, detected_flags)
            ]
            s = score.score_invoice(preds, list(gt.rows))
            invoice_scores.append(s)
            per_image.append({"image_id": sample.image_id,
                              "rows": s.rows_total, "rows_exact": s.rows_exact})
        except Exception as exc:   # 개별 이미지 실패 격리(§6)
            failures.append({"image_id": sample.image_id, "reason": f"error: {exc}"})

    metrics = score.aggregate(invoice_scores)
    data = report.ReportData(metrics=metrics, per_image=per_image,
                             failures=failures, rule_counts=rule_counts)
    return invoice_scores, data


def _cmd_run() -> None:
    from .db import parse_backup
    from .match import read_review_csv, resolve_ground_truth
    from .detect import PPStructureDetector
    from .recognize import PaddleOCRNumeric
    from PIL import Image

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    reviewed = read_review_csv(_RESULTS / "reviewed_dates.csv")
    gt = resolve_ground_truth(reviewed, db)
    samples = load_samples(config.images_dir(), config.labels_dir())
    _, data = run_pipeline(samples, gt, PPStructureDetector(),
                           PaddleOCRNumeric(), Image.open)
    report.write_report(data, _REPORT)
    print(f"[run] report → {_REPORT/'sp1-report.md'}  (정답지 {len(gt)}/{len(samples)})")


def _cmd_match_extract() -> None:
    """references OCR → total_supply(라벨 합) → reviewed_dates.csv. (검수 전)"""
    from .db import parse_backup
    from .match import build_review_rows, total_supply_from_labels, write_review_csv
    from .recognize import PaddleOCRText
    from .detect import PPStructureDetector  # references 도 표라 PP-Structure 텍스트 활용 가능
    from PIL import Image

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    samples = load_samples(config.images_dir(), config.labels_dir())
    recognizer = PaddleOCRText()
    detector = PPStructureDetector()
    per_image = []
    for sample in samples:
        ref_path = config.references_dir() / f"{sample.image_id}.jpg"
        texts: list[str] = []
        if ref_path.is_file():
            image = Image.open(ref_path)
            for cell in detector.detect(str(ref_path)):
                crop = crop_cell(image, cell.bbox)
                if crop is not None:
                    texts.append(recognizer.recognize(crop))
        total_supply = total_supply_from_labels(sample.label_rows)
        per_image.append((sample.image_id, texts, total_supply))
    rows = build_review_rows(per_image, db)
    write_review_csv(rows, _RESULTS / "reviewed_dates.csv")
    print(f"[match-extract] reviewed_dates.csv → {_RESULTS}  ({len(rows)}행) — 검수 후 run")


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
