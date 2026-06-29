"""SalesRecordRepository — PHP repositories/SalesRecordRepository.php 동형(text() raw SQL).

salesperson 조회(find_salesperson)는 snapshot_name 채움/존재 검증용으로 자기완결적으로
salespeople 테이블을 직접 읽는다(salespeople 리소스 파일을 import하지 않음 →
app.main이 형제 에이전트에게도 항상 import 가능). INT→int, TINYINT→int 1|0(bool 변환 금지).
"""

import calendar

from sqlalchemy import text

from app.db import connection


def _month_range(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _rows(result) -> list[dict]:
    return [dict(m) for m in result.mappings().all()]


class SalesRecordRepository:
    def find_records_by_month(self, year: int, month: int) -> list[dict]:
        start, end = _month_range(year, month)
        sql = """
            SELECT id, salesperson_id, work_date, quantity, snapshot_name
            FROM sales_records
            WHERE work_date BETWEEN :start AND :end
            ORDER BY work_date ASC, salesperson_id ASC
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), {"start": start, "end": end}))

    def find_by_key(self, salesperson_id: int, work_date: str) -> dict | None:
        with connection() as conn:
            row = (
                conn.execute(
                    text("""
                SELECT id, salesperson_id, work_date, quantity, snapshot_name
                FROM sales_records
                WHERE salesperson_id = :sp_id AND work_date = :work_date
            """),
                    {"sp_id": salesperson_id, "work_date": work_date},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def find_salespeople_for_month(self, year: int, month: int) -> list[dict]:
        start, end = _month_range(year, month)
        sql = """
            SELECT DISTINCT sp.id, sp.name, sp.sort_order, sp.is_active
            FROM salespeople sp
            LEFT JOIN sales_records sr
              ON sr.salesperson_id = sp.id
              AND sr.work_date BETWEEN :start AND :end
            WHERE sp.is_active = 1 OR sr.id IS NOT NULL
            ORDER BY sp.is_active DESC, sp.sort_order ASC, sp.id ASC
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), {"start": start, "end": end}))

    def find_salesperson(self, id: int) -> dict | None:
        with connection() as conn:
            row = (
                conn.execute(
                    text("SELECT id, name FROM salespeople WHERE id = :id"),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def upsert(
        self, salesperson_id: int, work_date: str, quantity: int, snapshot_name: str
    ) -> None:
        with connection() as conn:
            conn.execute(
                text("""
                INSERT INTO sales_records (salesperson_id, work_date, quantity, snapshot_name)
                VALUES (:sp_id, :work_date, :quantity, :snapshot_name)
                ON DUPLICATE KEY UPDATE
                    quantity = VALUES(quantity),
                    snapshot_name = VALUES(snapshot_name)
            """),
                {
                    "sp_id": salesperson_id,
                    "work_date": work_date,
                    "quantity": quantity,
                    "snapshot_name": snapshot_name,
                },
            )

    def delete(self, id: int) -> bool:
        with connection() as conn:
            return (
                conn.execute(text("DELETE FROM sales_records WHERE id = :id"), {"id": id}).rowcount
                > 0
            )
