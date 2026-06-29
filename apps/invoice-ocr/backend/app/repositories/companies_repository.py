"""CompanyRepository — PHP repositories/CompanyRepository.php 동형(text() raw SQL).

정렬 컬럼은 화이트리스트 매핑으로만 변환(문자열 보간 SQL injection 방어).
company_name UNIQUE → insert는 ON DUPLICATE KEY UPDATE upsert.
"""

from sqlalchemy import text

from app.db import connection

_ALLOWED_SORT_COLUMNS = {
    "company_name": "company_name",
    "usage_count": "usage_count",
    "last_used": "last_used",
    "created_at": "created_at",
}
# usage_count, last_used는 기본 DESC, 나머지는 ASC
_DESC_COLUMNS = {"usage_count", "last_used"}

_SELECT_COLUMNS = """
    id, company_name, recipient2, phone, fax, sms_number_type,
    address, business_number, notes, usage_count, last_used, created_at
"""


def _rows(result) -> list[dict]:
    return [dict(m) for m in result.mappings().all()]


def _sms_type(data: dict) -> str:
    v = data.get("sms_number_type")
    return v if v is not None else "phone"


class CompanyRepository:
    def find_all(self, filters: dict) -> list[dict]:
        where = "1=1"
        params: dict = {}
        if filters.get("q"):
            where += " AND (company_name LIKE :q1 OR business_number LIKE :q2)"
            params["q1"] = f"%{filters['q']}%"
            params["q2"] = f"%{filters['q']}%"

        column = _ALLOWED_SORT_COLUMNS.get(
            filters.get("sort_by") or "company_name", "company_name"
        )
        order = "DESC" if column in _DESC_COLUMNS else "ASC"
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM company_suggestions
            WHERE {where}
            ORDER BY {column} {order}
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))

    def find_by_id(self, id: int) -> dict | None:
        with connection() as conn:
            row = (
                conn.execute(
                    text(
                        f"SELECT {_SELECT_COLUMNS} FROM company_suggestions WHERE id = :id"
                    ),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def insert(self, data: dict) -> int:
        with connection() as conn:
            result = conn.execute(
                text("""
                INSERT INTO company_suggestions (
                    company_name, recipient2, phone, fax, sms_number_type,
                    address, business_number, notes
                ) VALUES (
                    :company_name, :recipient2, :phone, :fax, :sms_number_type,
                    :address, :business_number, :notes
                )
                ON DUPLICATE KEY UPDATE
                    recipient2 = VALUES(recipient2),
                    phone = VALUES(phone),
                    fax = VALUES(fax),
                    sms_number_type = VALUES(sms_number_type),
                    address = VALUES(address),
                    business_number = VALUES(business_number),
                    notes = VALUES(notes)
            """),
                {
                    "company_name": data["company_name"],
                    "recipient2": data.get("recipient2"),
                    "phone": data.get("phone"),
                    "fax": data.get("fax"),
                    "sms_number_type": _sms_type(data),
                    "address": data.get("address"),
                    "business_number": data.get("business_number"),
                    "notes": data.get("notes"),
                },
            )
            return int(result.lastrowid)

    def update(self, id: int, data: dict) -> bool:
        with connection() as conn:
            result = conn.execute(
                text("""
                UPDATE company_suggestions SET
                    company_name = :company_name,
                    recipient2 = :recipient2,
                    phone = :phone,
                    fax = :fax,
                    sms_number_type = :sms_number_type,
                    address = :address,
                    business_number = :business_number,
                    notes = :notes
                WHERE id = :id
            """),
                {
                    "id": id,
                    "company_name": data["company_name"],
                    "recipient2": data.get("recipient2"),
                    "phone": data.get("phone"),
                    "fax": data.get("fax"),
                    "sms_number_type": _sms_type(data),
                    "address": data.get("address"),
                    "business_number": data.get("business_number"),
                    "notes": data.get("notes"),
                },
            )
            return result.rowcount > 0

    def delete(self, id: int) -> bool:
        with connection() as conn:
            return (
                conn.execute(
                    text("DELETE FROM company_suggestions WHERE id = :id"), {"id": id}
                ).rowcount
                > 0
            )

    def increment_usage_by_name(self, company_name: str) -> None:
        with connection() as conn:
            conn.execute(
                text("""
                UPDATE company_suggestions
                SET usage_count = usage_count + 1, last_used = NOW()
                WHERE company_name = :name
            """),
                {"name": company_name},
            )

    def find_invoices_by_company_id(self, company_id: int) -> list[dict]:
        with connection() as conn:
            return _rows(
                conn.execute(
                    text("""
                SELECT i.* FROM invoices i
                WHERE i.recipient = (SELECT company_name FROM company_suggestions WHERE id = :id)
                ORDER BY i.issue_date DESC
            """),
                    {"id": company_id},
                )
            )
