"""InvoiceRepository — PHP repositories/InvoiceRepository.php 동형(text() raw SQL).

정렬 컬럼은 화이트리스트 매핑으로만 변환(문자열 보간 SQL injection 방어).
"""
from sqlalchemy import text

from app.db import connection

_ALLOWED_SORT_COLUMNS = {
    "issue_date": "i.issue_date",
    "grand_total": "i.grand_total",
    "recipient": "i.recipient",
    "created_at": "i.created_at",
}
_ALLOWED_SORT_ORDERS = {"asc", "desc"}


def _rows(result) -> list[dict]:
    return [dict(m) for m in result.mappings().all()]


class InvoiceRepository:
    def _where(self, filters: dict) -> tuple[str, dict]:
        where = "1=1"
        params: dict = {}
        if filters.get("search"):
            where += " AND (i.recipient LIKE :search1 OR i.vehicle_no LIKE :search2)"
            params["search1"] = f"%{filters['search']}%"
            params["search2"] = f"%{filters['search']}%"
        if filters.get("date_from"):
            where += " AND i.issue_date >= :date_from"
            params["date_from"] = filters["date_from"]
        if filters.get("date_to"):
            where += " AND i.issue_date <= :date_to"
            params["date_to"] = filters["date_to"]
        return where, params

    def find_all(self, filters: dict) -> list[dict]:
        where, params = self._where(filters)
        col = _ALLOWED_SORT_COLUMNS.get(filters["sort_by"], _ALLOWED_SORT_COLUMNS["issue_date"])
        order = filters["sort_order"] if filters["sort_order"] in _ALLOWED_SORT_ORDERS else "desc"
        params["limit"] = filters["limit"]
        params["offset"] = (filters["page"] - 1) * filters["limit"]
        sql = f"""
            SELECT i.id, i.document_title, i.issue_date, i.recipient, i.recipient2,
                   i.vehicle_no, i.memo, i.show_stamp, i.issuer_id,
                   i.total_supply, i.total_vat, i.grand_total, i.created_at, i.updated_at
            FROM invoices i
            WHERE {where}
            ORDER BY {col} {order}, i.id DESC
            LIMIT :limit OFFSET :offset
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))

    def count_all(self, filters: dict) -> int:
        where, params = self._where(filters)
        with connection() as conn:
            value = conn.execute(text(f"SELECT COUNT(*) FROM invoices i WHERE {where}"), params).scalar()
            return int(value or 0)

    def find_by_id(self, id: int) -> dict | None:
        with connection() as conn:
            row = conn.execute(text("SELECT * FROM invoices WHERE id = :id"), {"id": id}).mappings().first()
            return dict(row) if row else None

    def find_items(self, invoice_id: int) -> list[dict]:
        with connection() as conn:
            return _rows(conn.execute(
                text("SELECT * FROM invoice_items WHERE invoice_id = :id ORDER BY item_order"),
                {"id": invoice_id},
            ))

    def insert(self, data: dict) -> int:
        with connection() as conn:
            result = conn.execute(text("""
                INSERT INTO invoices (document_title, issue_date, recipient, recipient2, vehicle_no,
                    memo, show_stamp, issuer_id, total_supply, total_vat, grand_total)
                VALUES (:document_title, :issue_date, :recipient, :recipient2, :vehicle_no,
                    :memo, :show_stamp, :issuer_id, :total_supply, :total_vat, :grand_total)
            """), {
                "document_title": data.get("document_title", "거 래 명 세 서"),
                "issue_date": data["issue_date"],
                "recipient": data["recipient"],
                "recipient2": data.get("recipient2", ""),
                "vehicle_no": data.get("vehicle_no", ""),
                "memo": data.get("memo"),
                "show_stamp": 1 if data.get("show_stamp", 1) else 0,
                "issuer_id": data.get("issuer_id"),
                "total_supply": data.get("total_supply", 0),
                "total_vat": data.get("total_vat", 0),
                "grand_total": data.get("grand_total", 0),
            })
            return int(result.lastrowid)

    def insert_item(self, item: dict) -> None:
        with connection() as conn:
            conn.execute(text("""
                INSERT INTO invoice_items (invoice_id, item_order, name, quantity, unit,
                    unit_price, supply, vat, total, deduction)
                VALUES (:invoice_id, :item_order, :name, :quantity, :unit,
                    :unit_price, :supply, :vat, :total, :deduction)
            """), {
                "invoice_id": item["invoice_id"],
                "item_order": item["item_order"],
                "name": item["name"],
                "quantity": item.get("quantity", 0),
                "unit": item.get("unit", "EA"),
                "unit_price": item.get("unit_price", 0),
                "supply": item.get("supply", 0),
                "vat": item.get("vat", 0),
                "total": item.get("total", 0),
                "deduction": 1 if item.get("deduction") else 0,
            })

    def update(self, id: int, data: dict) -> bool:
        with connection() as conn:
            result = conn.execute(text("""
                UPDATE invoices SET document_title=:document_title, issue_date=:issue_date,
                    recipient=:recipient, recipient2=:recipient2, vehicle_no=:vehicle_no,
                    memo=:memo, show_stamp=:show_stamp, issuer_id=:issuer_id,
                    total_supply=:total_supply, total_vat=:total_vat, grand_total=:grand_total
                WHERE id=:id
            """), {
                "id": id,
                "document_title": data.get("document_title", "거 래 명 세 서"),
                "issue_date": data["issue_date"],
                "recipient": data["recipient"],
                "recipient2": data.get("recipient2", ""),
                "vehicle_no": data.get("vehicle_no", ""),
                "memo": data.get("memo"),
                "show_stamp": 1 if data.get("show_stamp", 1) else 0,
                "issuer_id": data.get("issuer_id"),
                "total_supply": data.get("total_supply", 0),
                "total_vat": data.get("total_vat", 0),
                "grand_total": data.get("grand_total", 0),
            })
            return result.rowcount > 0

    def delete_items(self, invoice_id: int) -> None:
        with connection() as conn:
            conn.execute(text("DELETE FROM invoice_items WHERE invoice_id = :id"), {"id": invoice_id})

    def delete(self, id: int) -> bool:
        with connection() as conn:
            return conn.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": id}).rowcount > 0

    def find_all_for_export(self, filters: dict) -> list[dict]:
        where = "1=1"
        params: dict = {}
        if filters.get("date_from"):
            where += " AND i.issue_date >= :date_from"
            params["date_from"] = filters["date_from"]
        if filters.get("date_to"):
            where += " AND i.issue_date <= :date_to"
            params["date_to"] = filters["date_to"]
        if filters.get("company_id"):
            where += " AND i.recipient = (SELECT company_name FROM company_suggestions WHERE id = :company_id)"
            params["company_id"] = filters["company_id"]
        sql = f"""
            SELECT i.id, i.document_title, i.issue_date, i.recipient, i.recipient2,
                   i.vehicle_no, i.memo, i.total_supply, i.total_vat, i.grand_total, i.created_at
            FROM invoices i
            WHERE {where}
            ORDER BY i.issue_date DESC, i.id DESC
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))
