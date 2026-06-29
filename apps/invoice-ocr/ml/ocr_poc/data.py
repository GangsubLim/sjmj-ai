"""이미지·라벨 페어 로딩. 라벨은 *_text 만 쓰고 geometry 는 무시한다(§2.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LabelRow:
    row_id: int
    item: str
    quantity: str
    unit_price: str
    amount: str


@dataclass(frozen=True)
class Sample:
    image_id: str
    image_path: Path
    label_rows: tuple[LabelRow, ...]


def load_label(label_json: dict) -> tuple[LabelRow, ...]:
    rows: list[LabelRow] = []
    for r in label_json.get("rows", []):
        rows.append(LabelRow(
            row_id=int(r.get("row_id", len(rows) + 1)),
            item=str(r.get("item_class", "")),
            quantity=str(r.get("quantity_text", "")),
            unit_price=str(r.get("unit_price_text", "")),
            amount=str(r.get("amount_text", "")),
        ))
    return tuple(rows)


def load_samples(images_dir: Path, labels_dir: Path) -> list[Sample]:
    """images/inv_*.jpg ↔ labels/inv_*.json 페어. 라벨 없는 이미지는 제외·경고."""
    samples: list[Sample] = []
    for img_path in sorted(images_dir.glob("inv_*.jpg")):
        image_id = img_path.stem
        label_path = labels_dir / f"{image_id}.json"
        if not label_path.is_file():
            print(f"[data] 경고: 라벨 없음 {label_path} — 건너뜀")
            continue
        label_json = json.loads(label_path.read_text(encoding="utf-8"))
        samples.append(Sample(
            image_id=image_id,
            image_path=img_path,
            label_rows=load_label(label_json),
        ))
    return samples
