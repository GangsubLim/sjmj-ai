from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.services.sales_records_service import SalesRecordService


def test_get_monthly_assembles_salespeople_and_records():
    repo = MagicMock()
    repo.find_salespeople_for_month.return_value = [
        {"id": 1, "name": "홍길동", "sort_order": 0, "is_active": 1}
    ]
    repo.find_records_by_month.return_value = [
        {
            "id": 10,
            "salesperson_id": 1,
            "work_date": "2026-05-15",
            "quantity": 1000,
            "snapshot_name": "홍길동",
        }
    ]
    result = SalesRecordService(repo).get_monthly(2026, 5)
    assert len(result["salespeople"]) == 1
    assert len(result["records"]) == 1
    assert result["records"][0]["quantity"] == 1000
    repo.find_salespeople_for_month.assert_called_once_with(2026, 5)
    repo.find_records_by_month.assert_called_once_with(2026, 5)


def test_upsert_record_captures_snapshot_name_and_returns_row():
    repo = MagicMock()
    repo.find_salesperson.return_value = {"id": 1, "name": "홍길동"}
    row = {
        "id": 42,
        "salesperson_id": 1,
        "work_date": "2026-05-15",
        "quantity": 1000000,
        "snapshot_name": "홍길동",
    }
    repo.find_by_key.return_value = row

    result = SalesRecordService(repo).upsert_record(1, "2026-05-15", 1000000)

    # snapshot_name은 클라가 아니라 salesperson.name으로 채워짐
    repo.upsert.assert_called_once_with(1, "2026-05-15", 1000000, "홍길동")
    assert result["id"] == 42
    assert result["snapshot_name"] == "홍길동"


def test_upsert_record_rejects_unknown_salesperson():
    repo = MagicMock()
    repo.find_salesperson.return_value = None

    with pytest.raises(AppError) as exc:
        SalesRecordService(repo).upsert_record(999, "2026-05-15", 100)

    assert exc.value.status == 404
    assert exc.value.code == "NOT_FOUND"
    repo.upsert.assert_not_called()


def test_delete_record_delegates():
    repo = MagicMock()
    repo.delete.return_value = True
    assert SalesRecordService(repo).delete_record(7) is True
    repo.delete.assert_called_once_with(7)


def test_delete_record_false_when_missing():
    repo = MagicMock()
    repo.delete.return_value = False
    assert SalesRecordService(repo).delete_record(999) is False
