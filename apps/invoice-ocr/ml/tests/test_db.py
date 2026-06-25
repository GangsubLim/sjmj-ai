from ocr_poc.db import parse_backup, Invoice, InvoiceItem


def test_parses_invoices_and_items(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    assert len(db.invoices) == 2
    inv11 = next(i for i in db.invoices if i.id == 11)
    assert inv11 == Invoice(id=11, issue_date="2026-05-12", recipient="옥천운수",
                            total_supply=300000, grand_total=330000)


def test_handles_null_and_escaped_quote(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    # recipient2 NULL, memo 에 escaped quote 가 있어도 행 경계가 깨지지 않는다
    items12 = db.items_for(12)
    assert len(items12) == 2
    assert items12[1].name == "중고타이어"   # unit 이 NULL 인 행도 정상 파싱


def test_find_by_date_and_total_unique(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    hits = db.find_by_date_and_total("2026-05-12", 330000)
    assert [h.id for h in hits] == [11]


def test_find_by_grand_total(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    assert [h.id for h in db.find_by_grand_total(132000)] == [12]


def test_items_ordered_by_item_order(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    orders = [it.item_order for it in db.items_for(12)]
    assert orders == [1, 2]


def test_semicolon_inside_value_does_not_drop_rows():
    sql = (
        "INSERT INTO `invoices` (`id`, `document_title`, `issue_date`, `recipient`, "
        "`recipient2`, `vehicle_no`, `memo`, `show_stamp`, `issuer_id`, `total_supply`, "
        "`total_vat`, `grand_total`, `created_at`, `updated_at`) VALUES\n"
        "(11, '거래명세서', '2026-05-12', 'A;B 상사', '이희원', '5608', '', 1, NULL, "
        "300000, 30000, 330000, '2026-05-12 05:57:39', '2026-05-12 05:57:39'),\n"
        "(12, '거래명세서', '2026-05-13', '성우항공', NULL, '3102', 'x;y', 1, NULL, "
        "120000, 12000, 132000, '2026-05-13 08:48:53', '2026-05-13 08:48:53');\n"
    )
    db = parse_backup(sql)
    assert len(db.invoices) == 2
    assert {i.id for i in db.invoices} == {11, 12}


def test_unquote_handles_escape_sequences():
    from ocr_poc.db import _unquote
    assert _unquote("NULL") is None
    assert _unquote("'O''Brien'") == "O'Brien"        # SQL 표준 더블
    assert _unquote(r"'O\'Brien'") == "O'Brien"       # 백슬래시 escape 따옴표
    assert _unquote(r"'a\\b'") == "a\\b"              # \\ = 리터럴 백슬래시 1개
    assert _unquote(r"'a\\nb'") == "a\\nb"            # \\ + n = 백슬래시+n (개행 아님)
    assert _unquote(r"'a\nb'") == "a\nb"              # \n = 개행
    assert _unquote(r"'C:\\temp'") == "C:\\temp"      # 윈도우 경로류
