"""references 인쇄일자 추출 → 사용자 검수(CSV) → 일자+total_supply DB 유일조회.

손라벨 행순서·geometry 를 쓰지 않는 정답지 매칭(§3). total_supply 는 라벨
amount_text 합에서 독립 산출(인식 결과 미사용 → 순환참조 없음).
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import db as dbmod
from .data import LabelRow

_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})"),
]


@dataclass(frozen=True)
class ReviewRow:
    image_id: str
    extracted_date: str | None
    total_supply: int
    db_match_count: int
    status: str   # unique | ambiguous | no_match


@dataclass(frozen=True)
class GroundTruth:
    image_id: str
    invoice_id: int
    rows: tuple[dbmod.InvoiceItem, ...]


def extract_date(texts: list[str]) -> str | None:
    """OCR 텍스트들에서 첫 날짜를 YYYY-MM-DD 로 정규화."""
    for text in texts:
        for pat in _DATE_PATTERNS:
            m = pat.search(text)
            if m:
                y, mo, d = m.groups()
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def total_supply_from_labels(rows: Iterable[LabelRow]) -> int:
    """라벨 amount_text 합(공급가합 = total_supply). DB 정답지 매칭키로 쓰인다.

    라벨 amount 는 공급가(VAT 제외)이므로 그 합은 DB total_supply 와 같다.
    VAT 포함 grand_total 과는 다르므로 매칭키로 grand_total 을 쓰면 안 된다.
    """
    total = 0
    for r in rows:
        digits = re.sub(r"[^0-9]", "", r.amount)
        if digits:
            total += int(digits)
    return total


def _status_for(count: int) -> str:
    if count == 1:
        return "unique"
    if count == 0:
        return "no_match"
    return "ambiguous"


def build_review_rows(
    per_image: list[tuple[str, list[str], int]],
    db: dbmod.InvoiceDB,
) -> list[ReviewRow]:
    """per_image: (image_id, references_ocr_texts, total_supply) → 검수행."""
    rows: list[ReviewRow] = []
    for image_id, texts, total_supply in per_image:
        date = extract_date(texts)
        if date is not None:
            hits = db.find_by_date_and_total_supply(date, total_supply)
        else:
            hits = db.find_by_total_supply(total_supply)
        rows.append(ReviewRow(
            image_id=image_id,
            extracted_date=date,
            total_supply=total_supply,
            db_match_count=len(hits),
            status=_status_for(len(hits)),
        ))
    return rows


_CSV_FIELDS = ["image_id", "extracted_date", "total_supply", "db_match_count", "status"]


def write_review_csv(rows: list[ReviewRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "image_id": r.image_id,
                "extracted_date": r.extracted_date or "",
                "total_supply": r.total_supply,
                "db_match_count": r.db_match_count,
                "status": r.status,
            })


def read_review_csv(path: Path) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append(ReviewRow(
                image_id=rec["image_id"],
                extracted_date=rec["extracted_date"] or None,
                total_supply=int(rec["total_supply"]),
                db_match_count=int(rec["db_match_count"]),
                status=rec["status"],
            ))
    return rows


def resolve_ground_truth(
    reviewed: list[ReviewRow],
    db: dbmod.InvoiceDB,
) -> dict[str, GroundTruth]:
    """검수된 행 중 일자+공급가합(total_supply)으로 유일 식별되는 것만 정답지로 채택."""
    out: dict[str, GroundTruth] = {}
    for r in reviewed:
        if r.extracted_date is None:
            hits = db.find_by_total_supply(r.total_supply)
        else:
            hits = db.find_by_date_and_total_supply(r.extracted_date, r.total_supply)
        if len(hits) != 1:
            continue
        inv = hits[0]
        out[r.image_id] = GroundTruth(
            image_id=r.image_id,
            invoice_id=inv.id,
            rows=db.items_for(inv.id),
        )
    return out
