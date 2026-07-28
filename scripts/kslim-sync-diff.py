#!/usr/bin/env python3
"""kslim 덤프 ↔ prod(sjmj) 거래명세서 내용 대조 도구 (폴백 catch-up 보조).

운영 모델: 신규 시스템(macmini prod)이 정본. macmini/신규 시스템이 down 되어
못 쓰는 동안만 기존 kslim 시스템에 비상 작성 → 그 덤프를 prod로 catch-up 병합한다.
id는 두 시스템에서 충돌하므로(각자 자체 채번), 매칭은 id가 아니라 내용
(발행일 + 수신처 + 공급가 + 합계)으로 한다. 방향은 kslim → prod 단방향.

이 스크립트는 kslim 덤프에서 prod에 '내용상 없는' 거래명세서만 골라 보여주고,
--emit-sql 로 prod 현재 max id 뒤에 붙일 append SQL 파일을 생성한다.

READ-ONLY: prod 는 SELECT 만 한다. append SQL 은 파일로 만들기만 하며,
적용은 사람이 검토 후 수동으로 한다(scp + `mysql < file` + 사후 검증).

주의(내용 대조의 한계): prod 에서 이미 import 된 명세서를 '수정'하면 그 건의
내용 키가 kslim 과 달라져 '누락'으로 오검출될 수 있다. down 기간이 명확하면
--since <복구직전 발행일> 로 범위를 좁혀 오검출을 줄인다.

사용:
  scripts/kslim-sync-diff.py [DUMP] [--host macmini] [--since YYYY-MM-DD]
                             [--emit-sql out.sql] [--show-prod-only]
  DUMP 생략 시 ~/Downloads/kslim-*.sql 중 가장 최신 파일을 쓴다.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

# invoices 는 덤프와 prod 가 14컬럼 1:1. invoice_items 는 prod 에만 deduction 컬럼이
# 추가돼 있으므로 append 시 아래 10컬럼만 명시 삽입하고 deduction 은 default(0)에 맡긴다.
ITEM_INSERT_COLS = [
    "id",
    "invoice_id",
    "item_order",
    "name",
    "quantity",
    "unit",
    "unit_price",
    "supply",
    "vat",
    "total",
]

REMOTE_MYSQL = (
    "source ~/.sjmj-ai/backend.env && "
    'MYSQL_PWD="$DB_PASS" /opt/homebrew/bin/mysql '
    "--default-character-set=utf8mb4 -N "
    '-h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME"'
)


# ---------------------------------------------------------------------------
# 덤프 파서 (phpMyAdmin INSERT 문 tokenizer — 문자열 내 콤마/괄호/한글 안전)
# 값은 ('str'|'num'|'null', value) 튜플로 표현해 재출력 시 타입을 보존한다.
# ---------------------------------------------------------------------------
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def _read_value(text: str, pos: int) -> tuple[tuple[str, object], int]:
    n = len(text)
    if text[pos] == "'":
        pos += 1
        buf: list[str] = []
        while pos < n:
            c = text[pos]
            if c == "\\" and pos + 1 < n:  # 백슬래시 이스케이프
                buf.append(_ESCAPES.get(text[pos + 1], text[pos + 1]))
                pos += 2
                continue
            if c == "'":
                if pos + 1 < n and text[pos + 1] == "'":  # '' → '
                    buf.append("'")
                    pos += 2
                    continue
                pos += 1
                break
            buf.append(c)
            pos += 1
        return ("str", "".join(buf)), pos
    start = pos
    while pos < n and text[pos] not in ",)":
        pos += 1
    raw = text[start:pos].strip()
    if raw.upper() == "NULL":
        return ("null", None), pos
    return ("num", raw), pos


def _read_tuple(text: str, pos: int) -> tuple[list, int]:
    pos += 1  # skip '('
    n, values = len(text), []
    while pos < n:
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if text[pos] == ")":
            return values, pos + 1
        val, pos = _read_value(text, pos)
        values.append(val)
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos < n and text[pos] == ",":
            pos += 1
    return values, pos


def _read_rows(text: str, pos: int) -> tuple[list, int]:
    n, rows = len(text), []
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] == ";":
            return rows, pos + 1
        if text[pos] != "(":
            return rows, pos
        row, pos = _read_tuple(text, pos)
        rows.append(row)
    return rows, pos


def parse_table(text: str, table: str) -> list[dict]:
    """덤프 텍스트에서 `table` 의 모든 INSERT 행을 {컬럼: (kind, val)} dict 리스트로."""
    marker = f"INSERT INTO `{table}`"
    idx, out = 0, []
    while True:
        p = text.find(marker, idx)
        if p < 0:
            return out
        lp = text.find("(", p + len(marker))
        rp = text.find(")", lp)
        cols = [c.strip().strip("`") for c in text[lp + 1 : rp].split(",")]
        vpos = text.find("VALUES", rp) + len("VALUES")
        rows, idx = _read_rows(text, vpos)
        for row in rows:
            out.append(dict(zip(cols, row)))


# ---------------------------------------------------------------------------
# 내용 키 + prod 조회
# ---------------------------------------------------------------------------
def _s(v) -> str:
    """(kind, val) 을 비교용 문자열로. null/None → ''."""
    if v is None:
        return ""
    _, val = v
    return "" if val is None else str(val)


def content_key(inv: dict) -> tuple:
    return (
        _s(inv.get("issue_date")),
        _s(inv.get("recipient")),
        _s(inv.get("recipient2")),
        _s(inv.get("total_supply")),
        _s(inv.get("grand_total")),
    )


def prod_query(sql: str, host: str) -> list[list[str]]:
    r = subprocess.run(
        ["ssh", host, REMOTE_MYSQL], input=sql, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"prod 조회 실패: {r.stderr.strip()}")
    return [ln.split("\t") for ln in r.stdout.splitlines() if ln]


def prod_keys(host: str) -> set[tuple]:
    rows = prod_query(
        "SELECT DATE_FORMAT(issue_date,'%Y-%m-%d'), recipient, "
        "IFNULL(recipient2,''), total_supply, grand_total FROM invoices;",
        host,
    )
    return {tuple(r) for r in rows}


def prod_next_ids(host: str) -> tuple[int, int]:
    """append 시작 id = 각 테이블 MAX(id)+1 (information_schema AI 캐시 지연 회피)."""
    inv = prod_query("SELECT COALESCE(MAX(id),0)+1 FROM invoices;", host)[0][0]
    item = prod_query("SELECT COALESCE(MAX(id),0)+1 FROM invoice_items;", host)[0][0]
    return int(inv), int(item)


# ---------------------------------------------------------------------------
# SQL 생성
# ---------------------------------------------------------------------------
def sql_lit(v) -> str:
    if v is None:
        return "NULL"
    kind, val = v
    if kind == "null":
        return "NULL"
    if kind == "num":
        return val
    return "'" + str(val).replace("\\", "\\\\").replace("'", "''") + "'"


def emit_sql(
    missing: list[dict], items_by_inv: dict, inv_id0: int, item_id0: int
) -> str:
    inv_cols = [
        "id",
        "document_title",
        "issue_date",
        "recipient",
        "recipient2",
        "vehicle_no",
        "memo",
        "show_stamp",
        "issuer_id",
        "total_supply",
        "total_vat",
        "grand_total",
        "created_at",
        "updated_at",
    ]
    inv_lines, item_lines = [], []
    next_inv, next_item = inv_id0, item_id0
    for inv in missing:
        new_id = next_inv
        next_inv += 1
        vals = [str(new_id) if c == "id" else sql_lit(inv.get(c)) for c in inv_cols]
        inv_lines.append(f"  ({', '.join(vals)})")
        for it in items_by_inv.get(_s(inv["id"]), []):
            row = []
            for c in ITEM_INSERT_COLS:
                if c == "id":
                    row.append(str(next_item))
                elif c == "invoice_id":
                    row.append(str(new_id))
                else:
                    row.append(sql_lit(it.get(c)))
            item_lines.append(f"  ({', '.join(row)})")
            next_item += 1

    parts = [
        "-- kslim 덤프 → prod append (내용상 prod 에 없는 건만). READ 후 수동 적용.",
        "-- 적용 전 반드시 대상 id 공백 재확인, 적용 후 ANALYZE TABLE + AUTO_INCREMENT 확인.",
        "START TRANSACTION;",
        "",
        "INSERT INTO invoices",
        f"  ({', '.join(inv_cols)})",
        "VALUES",
        ",\n".join(inv_lines) + ";",
        "",
        "INSERT INTO invoice_items",
        f"  ({', '.join(ITEM_INSERT_COLS)})",
        "VALUES",
        ",\n".join(item_lines) + ";",
        "",
        "COMMIT;",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
def resolve_dump(arg: str | None) -> Path:
    if arg:
        p = Path(arg).expanduser()
        if not p.is_file():
            sys.exit(f"덤프 파일 없음: {p}")
        return p
    cands = glob.glob(os.path.expanduser("~/Downloads/kslim-*.sql"))
    if not cands:
        sys.exit("~/Downloads/kslim-*.sql 을 찾을 수 없음. 덤프 경로를 인자로 넘겨라.")
    return Path(max(cands, key=os.path.getmtime))


def main() -> int:
    ap = argparse.ArgumentParser(description="kslim 덤프 ↔ prod 거래명세서 내용 대조")
    ap.add_argument("dump", nargs="?", help="kslim 덤프 경로(생략 시 ~/Downloads 최신)")
    ap.add_argument("--host", default="macmini", help="prod ssh 호스트 (기본 macmini)")
    ap.add_argument("--since", help="이 발행일(YYYY-MM-DD) 이상만 대조 — 오검출 축소")
    ap.add_argument("--emit-sql", metavar="PATH", help="append SQL 파일 생성")
    ap.add_argument(
        "--show-prod-only",
        action="store_true",
        help="prod 에만 있고 kslim 에 없는 건도 표시(방향성 확인용)",
    )
    args = ap.parse_args()

    dump = resolve_dump(args.dump)
    text = dump.read_text(encoding="utf-8", errors="replace")
    invoices = parse_table(text, "invoices")
    items = parse_table(text, "invoice_items")
    if not invoices:
        sys.exit(f"덤프에서 invoices 를 파싱하지 못했다: {dump}")

    items_by_inv: dict[str, list[dict]] = {}
    for it in items:
        items_by_inv.setdefault(_s(it["invoice_id"]), []).append(it)

    kslim = invoices
    if args.since:
        kslim = [i for i in kslim if _s(i["issue_date"]) >= args.since]

    pkeys = prod_keys(args.host)
    missing = [i for i in kslim if content_key(i) not in pkeys]
    missing.sort(key=lambda i: int(_s(i["id"])))

    print(f"덤프:  {dump}")
    print(
        f"파싱:  invoices {len(invoices)}건, invoice_items {len(items)}건"
        + (
            f" (issue_date >= {args.since} 필터 후 {len(kslim)}건 대조)"
            if args.since
            else ""
        )
    )
    print(f"prod:  invoices {len(pkeys)}건")
    print(f"누락(kslim→prod): {len(missing)}건")
    print("-" * 72)

    for inv in missing:
        its = items_by_inv.get(_s(inv["id"]), [])
        isum = sum(int(_s(it.get("supply")) or 0) for it in its)
        hdr = int(_s(inv.get("total_supply")) or 0)
        flag = "" if isum == hdr else f"  ⚠ 품목합 {isum} ≠ 헤더 {hdr}"
        print(
            f"  kslim#{_s(inv['id']):>4}  {_s(inv['issue_date'])}  "
            f"{_s(inv['recipient'])}  공급 {hdr:,}  품목 {len(its)}{flag}"
        )
    if not missing:
        print("  (없음 — prod 가 최신)")

    if args.show_prod_only:
        kkeys = {content_key(i) for i in invoices}
        prows = prod_query(
            "SELECT id, DATE_FORMAT(issue_date,'%Y-%m-%d'), recipient, "
            "IFNULL(recipient2,''), total_supply, grand_total FROM invoices "
            "ORDER BY id;",
            args.host,
        )
        ponly = [r for r in prows if tuple(r[1:]) not in kkeys]
        print("-" * 72)
        print(f"prod 전용(kslim 덤프에 없음): {len(ponly)}건")
        for r in ponly:
            print(f"  prod#{r[0]:>4}  {r[1]}  {r[2]}  공급 {int(r[4]):,}")

    if args.emit_sql:
        if not missing:
            print("\n생성할 append 없음(누락 0).")
            return 0
        inv0, item0 = prod_next_ids(args.host)
        out = Path(args.emit_sql).expanduser()
        out.write_text(emit_sql(missing, items_by_inv, inv0, item0), encoding="utf-8")
        print(f"\nappend SQL 생성: {out}")
        print(
            f"  invoices id {inv0}~{inv0 + len(missing) - 1}, "
            f"invoice_items id {item0}~ 부터 채번"
        )
        print(
            "  적용:  scp 로 macmini 전송 후 `mysql < file` (검토 필수, 자동적용 아님)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
