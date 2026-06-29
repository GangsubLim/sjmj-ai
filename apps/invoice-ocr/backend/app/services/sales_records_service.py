"""SalesRecordService — PHP services/SalesRecordService.php 동형.

salesperson 조회는 repo.find_salesperson로 자기완결(별도 SalespersonRepository import 없음
→ app.main import 안전). 존재하지 않는 영업사원이면 404. snapshot_name은 클라 입력을
무시하고 서버가 salesperson.name으로 채워 upsert한다.
"""

from app.core.errors import not_found
from app.repositories.sales_records_repository import SalesRecordRepository


class SalesRecordService:
    def __init__(self, repo=None):
        self.repo = repo or SalesRecordRepository()

    def get_monthly(self, year: int, month: int) -> dict:
        return {
            "salespeople": self.repo.find_salespeople_for_month(year, month),
            "records": self.repo.find_records_by_month(year, month),
        }

    def upsert_record(self, salesperson_id: int, work_date: str, quantity: int) -> dict | None:
        sp = self.repo.find_salesperson(salesperson_id)
        if not sp:
            not_found("영업사원을 찾을 수 없습니다.")
        self.repo.upsert(salesperson_id, work_date, quantity, sp["name"])
        return self.repo.find_by_key(salesperson_id, work_date)

    def delete_record(self, id: int) -> bool:
        return self.repo.delete(id)
