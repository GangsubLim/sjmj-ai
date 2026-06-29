"""운영 DB 백업(.sql, phpMyAdmin)의 invoices/invoice_items 파싱·조회.

외부 DB 드라이버 없이 표준 라이브러리만으로 INSERT VALUES 를 견고히 파싱한다.
러프 정규식은 멀티행 VALUES·escaped quote·NULL 에서 행을 누락하므로,
따옴표/괄호를 추적하는 문자 스캐너로 전 행을 보장한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Invoice:
    id: int
    issue_date: str
    recipient: str
    total_supply: int
    grand_total: int


@dataclass(frozen=True)
class InvoiceItem:
    invoice_id: int
    item_order: int
    name: str
    quantity: int
    unit_price: int
    supply: int
    vat: int
    total: int


@dataclass(frozen=True)
class InvoiceDB:
    invoices: tuple[Invoice, ...]
    items_by_invoice: dict[int, tuple[InvoiceItem, ...]]

    def find_by_grand_total(self, grand_total: int) -> list[Invoice]:
        return [i for i in self.invoices if i.grand_total == grand_total]

    def find_by_date_and_total(self, date: str, grand_total: int) -> list[Invoice]:
        return [i for i in self.invoices if i.issue_date == date and i.grand_total == grand_total]

    def find_by_total_supply(self, total_supply: int) -> list[Invoice]:
        return [i for i in self.invoices if i.total_supply == total_supply]

    def find_by_date_and_total_supply(self, date: str, total_supply: int) -> list[Invoice]:
        return [i for i in self.invoices if i.issue_date == date and i.total_supply == total_supply]

    def items_for(self, invoice_id: int) -> tuple[InvoiceItem, ...]:
        return self.items_by_invoice.get(invoice_id, ())


_INSERT_HEADER_RE = re.compile(r"INSERT INTO `(?P<table>\w+)` \((?P<cols>[^)]*)\) VALUES\s*")


def _read_statement_body(text: str, start: int) -> tuple[str, int]:
    """start(=VALUES 직후)부터 따옴표/괄호를 인지하며 스캔해, 문자열 밖·괄호 depth 0
    에서 처음 만나는 ';' 직전까지를 본문으로 반환. 값 내부의 ';'는 종결자로 보지 않는다.
    """
    in_str = False
    esc = False
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
        else:
            if ch == "'":
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                return text[start:i], i
        i += 1
    return text[start:i], i


def _split_top_level_groups(blob: str) -> list[str]:
    """`(...),(...)` 최상위 괄호 그룹들의 내부 문자열 리스트. 따옴표/escape 인지."""
    groups: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    esc = False
    for ch in blob:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "(":
            if depth == 0:
                buf = []
            else:
                buf.append(ch)
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                groups.append("".join(buf))
            else:
                buf.append(ch)
        elif depth > 0:
            buf.append(ch)
    return groups


def _split_fields(row: str) -> list[str]:
    """행 내부를 최상위 콤마로 분할(따옴표/escape 인지)."""
    fields: list[str] = []
    buf: list[str] = []
    in_str = False
    esc = False
    for ch in row:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == ",":
            fields.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf).strip())
    return fields


_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def _unquote(field: str) -> str | None:
    """SQL 값 토큰 → 파이썬 값(str|None). 숫자는 호출부에서 int 변환.

    인용 문자열 내부를 좌→우 1패스로 스캔해 escape 시퀀스(\\\\, \\', \\n 등)와
    SQL 표준 더블('')을 atomic하게 소비한다. chained replace 는 \\\\n 류를
    오변환하므로 쓰지 않는다.
    """
    if field == "NULL":
        return None
    if len(field) >= 2 and field[0] == "'" and field[-1] == "'":
        inner = field[1:-1]
        out: list[str] = []
        i = 0
        n = len(inner)
        while i < n:
            ch = inner[i]
            if ch == "\\" and i + 1 < n:
                out.append(_ESCAPES.get(inner[i + 1], inner[i + 1]))
                i += 2
                continue
            if ch == "'" and i + 1 < n and inner[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    return field


def _to_int(field: str) -> int:
    val = _unquote(field)
    return int(val) if val not in (None, "") else 0


def _rows_for_table(sql_text: str, table: str) -> list[tuple[list[str], list[str]]]:
    """(컬럼명 리스트, 필드 토큰 리스트) 행들."""
    out: list[tuple[list[str], list[str]]] = []
    for m in _INSERT_HEADER_RE.finditer(sql_text):
        if m.group("table") != table:
            continue
        cols = [c.strip().strip("`") for c in m.group("cols").split(",")]
        body, _ = _read_statement_body(sql_text, m.end())
        for group in _split_top_level_groups(body):
            out.append((cols, _split_fields(group)))
    return out


def parse_backup(sql_text: str) -> InvoiceDB:
    invoices: list[Invoice] = []
    for cols, fields in _rows_for_table(sql_text, "invoices"):
        rec = dict(zip(cols, fields))
        invoices.append(
            Invoice(
                id=_to_int(rec["id"]),
                issue_date=_unquote(rec["issue_date"]) or "",
                recipient=_unquote(rec["recipient"]) or "",
                total_supply=_to_int(rec["total_supply"]),
                grand_total=_to_int(rec["grand_total"]),
            )
        )

    items: dict[int, list[InvoiceItem]] = {}
    for cols, fields in _rows_for_table(sql_text, "invoice_items"):
        rec = dict(zip(cols, fields))
        item = InvoiceItem(
            invoice_id=_to_int(rec["invoice_id"]),
            item_order=_to_int(rec["item_order"]),
            name=_unquote(rec["name"]) or "",
            quantity=_to_int(rec["quantity"]),
            unit_price=_to_int(rec["unit_price"]),
            supply=_to_int(rec["supply"]),
            vat=_to_int(rec["vat"]),
            total=_to_int(rec["total"]),
        )
        items.setdefault(item.invoice_id, []).append(item)

    items_sorted = {
        inv_id: tuple(sorted(rows, key=lambda r: r.item_order)) for inv_id, rows in items.items()
    }
    return InvoiceDB(invoices=tuple(invoices), items_by_invoice=items_sorted)
