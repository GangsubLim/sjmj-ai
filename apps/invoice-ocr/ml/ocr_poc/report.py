"""측정 리포트(md+json) 산출. 오답 갤러리/검출 시각화 저장은 별도 헬퍼."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_METRIC_LABELS = {
    "detection_recall": "검출 리콜",
    "recognition_accuracy": "인식 정확도(검출 한정)",
    "validation_gain_accuracy": "검산 후 정확도(게인)",
    "row_exact_rate": "행 완전일치율",
}


@dataclass(frozen=True)
class ReportData:
    metrics: dict[str, float]
    per_image: list[dict]
    failures: list[dict]
    rule_counts: dict[str, int]


def render_markdown(data: ReportData) -> str:
    lines = ["# SP1 측정 리포트", "", "## 3축 메트릭", "", "| 지표 | 값 |", "| --- | --- |"]
    for key, label in _METRIC_LABELS.items():
        if key in data.metrics:
            lines.append(f"| {label} | {data.metrics[key]:.2f} |")
    lines += ["", "## 약식 규칙 적용", "", "| 규칙 | 횟수 |", "| --- | --- |"]
    for rule, cnt in sorted(data.rule_counts.items()):
        lines.append(f"| {rule} | {cnt} |")
    lines += ["", "## 명세서별", "", "| image | rows | rows_exact |", "| --- | --- | --- |"]
    for r in data.per_image:
        lines.append(f"| {r['image_id']} | {r.get('rows', 0)} | {r.get('rows_exact', 0)} |")
    lines += ["", "## 실패/제외 목록", ""]
    if data.failures:
        for f in data.failures:
            lines.append(f"- {f['image_id']}: {f.get('reason', '')}")
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def render_json(data: ReportData) -> str:
    return json.dumps({
        "metrics": data.metrics,
        "per_image": data.per_image,
        "failures": data.failures,
        "rule_counts": data.rule_counts,
    }, ensure_ascii=False, indent=2)


def write_report(data: ReportData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sp1-report.md").write_text(render_markdown(data), encoding="utf-8")
    (out_dir / "sp1-report.json").write_text(render_json(data), encoding="utf-8")
