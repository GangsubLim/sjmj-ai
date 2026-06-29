import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from app.repositories.sales_records_repository import SalesRecordRepository
from tests.fixtures import sales_records_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _insert_salesperson(overrides: dict | None = None) -> int:
    sp = td.salesperson(overrides)
    with db.connection() as conn:
        result = conn.execute(
            text(
                "INSERT INTO salespeople (name, sort_order, is_active) VALUES (:name, :sort_order, :is_active)"
            ),
            sp,
        )
        return int(result.lastrowid)


def test_upsert_inserts_then_updates():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson()

    repo.upsert(sp_id, "2026-05-15", 1000000, "영업사원1")
    rows = repo.find_records_by_month(2026, 5)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 1000000  # INT → int

    repo.upsert(sp_id, "2026-05-15", 2000000, "영업사원1")
    rows = repo.find_records_by_month(2026, 5)
    assert len(rows) == 1  # UNIQUE(salesperson_id, work_date) → 갱신, 중복 없음
    assert rows[0]["quantity"] == 2000000


def test_find_records_by_month_scopes_by_month():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson()
    repo.upsert(sp_id, "2026-05-01", 100, "영업사원1")
    repo.upsert(sp_id, "2026-06-01", 200, "영업사원1")

    may = repo.find_records_by_month(2026, 5)
    assert len(may) == 1
    assert may[0]["work_date"].isoformat() == "2026-05-01"


def test_delete_removes_record():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson()
    repo.upsert(sp_id, "2026-05-15", 100, "영업사원1")
    row = repo.find_records_by_month(2026, 5)[0]

    assert repo.delete(int(row["id"])) is True
    assert repo.find_records_by_month(2026, 5) == []


def test_delete_false_when_missing():
    assert SalesRecordRepository().delete(999999) is False


def test_find_by_key_returns_row():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson()
    repo.upsert(sp_id, "2026-05-15", 250000, "영업사원1")

    row = repo.find_by_key(sp_id, "2026-05-15")
    assert row is not None
    assert row["quantity"] == 250000
    assert row["snapshot_name"] == "영업사원1"


def test_find_by_key_none_when_missing():
    sp_id = _insert_salesperson()
    assert SalesRecordRepository().find_by_key(sp_id, "2099-01-01") is None


def test_find_salesperson_returns_id_and_name():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson({"name": "홍길동"})
    sp = repo.find_salesperson(sp_id)
    assert sp["id"] == sp_id
    assert sp["name"] == "홍길동"


def test_find_salesperson_none_when_missing():
    assert SalesRecordRepository().find_salesperson(999999) is None


def test_find_salespeople_for_month_includes_active_and_record_holders():
    repo = SalesRecordRepository()
    _insert_salesperson({"name": "Active", "sort_order": 1, "is_active": 1})
    inactive_id = _insert_salesperson(
        {"name": "Inactive", "sort_order": 2, "is_active": 0}
    )
    repo.upsert(inactive_id, "2026-05-10", 500, "Inactive")

    names = [s["name"] for s in repo.find_salespeople_for_month(2026, 5)]
    assert "Active" in names  # is_active=1
    assert "Inactive" in names  # 비활성이지만 해당월 실적 보유


def test_find_salespeople_for_month_excludes_inactive_without_records():
    repo = SalesRecordRepository()
    _insert_salesperson({"name": "Active", "is_active": 1})
    _insert_salesperson({"name": "Inactive", "is_active": 0})

    names = [s["name"] for s in repo.find_salespeople_for_month(2026, 5)]
    assert "Active" in names
    assert "Inactive" not in names  # 비활성 & 실적없음 → 제외


def test_find_salespeople_for_month_tinyint_is_int_not_bool():
    repo = SalesRecordRepository()
    _insert_salesperson({"name": "X", "is_active": 1})
    sp = repo.find_salespeople_for_month(2026, 5)[0]
    assert sp["is_active"] == 1
    assert sp["is_active"] is not True  # TINYINT → int 1, bool 변환 금지


def test_fk_restrict_prevents_parent_delete():
    repo = SalesRecordRepository()
    sp_id = _insert_salesperson()
    repo.upsert(sp_id, "2026-05-15", 100, "영업사원1")

    # ON DELETE RESTRICT: 실적이 있는 영업사원의 부모 행 삭제는 거부된다
    with pytest.raises(IntegrityError):
        with db.connection() as conn:
            conn.execute(text("DELETE FROM salespeople WHERE id = :id"), {"id": sp_id})
