import json
from pathlib import Path

from ocr_poc.report import ReportData, render_markdown, render_json, write_report


def _data():
    return ReportData(
        metrics={"detection_recall": 0.83, "recognition_accuracy": 0.8,
                 "validation_gain_accuracy": 0.9, "row_exact_rate": 0.5},
        per_image=[{"image_id": "inv_003", "rows": 10, "rows_exact": 6}],
        failures=[{"image_id": "inv_015", "reason": "no_match"}],
        rule_counts={"thousand_mult": 12, "blank_zero": 3, "ditto": 1},
    )


def test_render_markdown_has_three_axis_and_failures():
    md = render_markdown(_data())
    assert "검출 리콜" in md and "0.83" in md
    assert "인식 정확도" in md
    assert "검산" in md
    assert "inv_015" in md          # 실패목록 노출
    assert "thousand_mult" in md    # 약식 적용률


def test_render_json_roundtrips():
    payload = json.loads(render_json(_data()))
    assert payload["metrics"]["detection_recall"] == 0.83
    assert payload["rule_counts"]["thousand_mult"] == 12


def test_write_report_creates_files(tmp_path: Path):
    write_report(_data(), tmp_path)
    assert (tmp_path / "sp1-report.md").is_file()
    assert (tmp_path / "sp1-report.json").is_file()
