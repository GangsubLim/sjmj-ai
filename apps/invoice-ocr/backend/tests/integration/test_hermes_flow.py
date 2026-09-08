"""hermes 슬라이스 통합 — 실 MySQL(sjmj_test) + tmp 데이터 루트로 파일↔DB 전 구간.

핵심 회귀: 품목만 교정한 건(부모 invoices 행이 안 움직여 updated_at == created_at)이
mismatch로 잡히는지. 상태를 타임스탬프로 되돌리면 여기가 red다(spec §3.2·§7.1).
"""

import json

import pytest
from sqlalchemy import text

from app.services.hermes_service import HermesService

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    d = tmp_path / "agent_uploads"
    d.mkdir()
    return d


def _insert_invoice(engine, *, recipient, grand_total, items) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO invoices (issue_date, recipient, grand_total) "
                "VALUES ('2026-09-05', :r, :g)"
            ),
            {"r": recipient, "g": grand_total},
        )
        invoice_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        for order, (name, supply) in enumerate(items):
            conn.execute(
                text(
                    "INSERT INTO invoice_items "
                    "(invoice_id, item_order, name, quantity, unit, unit_price, supply) "
                    "VALUES (:i, :o, :n, 1, 'EA', :s, :s)"
                ),
                {"i": invoice_id, "o": order, "n": name, "s": supply},
            )
    return invoice_id


def _write_draft(uploads, invoice_id, *, recipient, grand_total, items):
    (uploads / f"{invoice_id}.draft.json").write_text(
        json.dumps(
            {
                "issue_date": "2026-09-05",
                "recipient": recipient,
                "grand_total": grand_total,
                "items": [{"name": n, "supply": s} for n, s in items],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_items_only_edit_is_mismatch_even_though_parent_row_untouched(uploads, db_conn):
    """품목 교정은 delete_items + 재삽입이라 부모가 그대로다 — 타임스탬프로는 못 잡는다."""
    invoice_id = _insert_invoice(
        db_conn, recipient="○○상사", grand_total=165000, items=[("히터", 150000)]
    )
    _write_draft(
        uploads, invoice_id, recipient="○○상사", grand_total=165000, items=[("히타", 150000)]
    )

    # 전제 확인: 부모 행의 두 타임스탬프가 실제로 같다(초 정밀도 TIMESTAMP).
    with db_conn.begin() as conn:
        same = conn.execute(
            text("SELECT created_at = updated_at FROM invoices WHERE id = :i"), {"i": invoice_id}
        ).scalar()
    assert same == 1

    entries, total = HermesService().list_entries(1, 20, None)
    assert total == 1
    assert entries[0]["status"] == "mismatch"
    assert entries[0]["mismatch_fields"] == ["name"]


def test_summary_joins_files_and_db_across_three_statuses(uploads, db_conn):
    matched = _insert_invoice(db_conn, recipient="가상사", grand_total=100, items=[("품목A", 100)])
    edited = _insert_invoice(db_conn, recipient="나상사", grand_total=200, items=[("품목B", 200)])
    _write_draft(uploads, matched, recipient="가상사", grand_total=100, items=[("품목A", 100)])
    _write_draft(uploads, edited, recipient="나상사", grand_total=200, items=[("품목오독", 200)])
    _write_draft(uploads, 999_100, recipient="다상사", grand_total=300, items=[("품목C", 300)])

    data = HermesService().summary()
    assert data["totals"]["count"] == 2  # deleted는 집계 분모에서 빠진다
    assert data["totals"]["missing"] == 1
    assert data["totals"]["untouched"] == 1
    assert data["totals"]["name_hits"] == 1
    assert data["totals"]["name_rate"] == 0.5

    statuses = {e["id"]: e["status"] for e in HermesService().list_entries(1, 20, None)[0]}
    assert statuses == {matched: "match", edited: "mismatch", 999_100: "deleted"}


def test_detail_reads_full_item_columns_without_new_sql(uploads, db_conn):
    """상세는 InvoiceRepository.find_items(SELECT *)를 재사용해 단가·수량까지 실어야 한다."""
    invoice_id = _insert_invoice(
        db_conn, recipient="가상사", grand_total=100, items=[("품목A", 100)]
    )
    _write_draft(uploads, invoice_id, recipient="가상사", grand_total=100, items=[("품목A", 100)])
    row = HermesService().get_entry(invoice_id)["rows"][0]
    assert row["final"] == {
        "name": "품목A",
        "quantity": 1,
        "unit": "EA",
        "unit_price": 100,
        "supply": 100,
        "deduction": False,
    }
