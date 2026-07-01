"""SalesRecordService.

salesperson 조회는 repo.find_salesperson로 자기완결(별도 SalespersonRepository import 없음
→ app.main import 안전). 존재하지 않는 영업사원이면 404. snapshot_name은 클라 입력을
무시하고 서버가 salesperson.name으로 채워 upsert한다.
"""

from app.core.errors import not_found
from app.repositories.sales_records_repository import SalesRecordRepository


class SalesRecordService:
    """영업사원 일별 판매 실적의 월간 조회·upsert·삭제를 담당하는 서비스."""

    def __init__(self, repo=None):
        """SalesRecordRepository를 주입받거나 기본 인스턴스를 생성한다."""
        self.repo = repo or SalesRecordRepository()

    def get_monthly(self, year: int, month: int) -> dict:
        """지정한 연·월의 영업사원 목록과 판매 실적을 함께 조회한다."""
        return {
            "salespeople": self.repo.find_salespeople_for_month(year, month),
            "records": self.repo.find_records_by_month(year, month),
        }

    def upsert_record(self, salesperson_id: int, work_date: str, quantity: int) -> dict | None:
        """영업사원의 해당 일자 실적을 upsert하고 갱신된 행을 반환한다.

        snapshot_name은 클라 입력을 무시하고 salesperson.name으로 채운다.
        존재하지 않는 영업사원이면 404를 발생시킨다.
        """
        sp = self.repo.find_salesperson(salesperson_id)
        if not sp:
            not_found("영업사원을 찾을 수 없습니다.")
        self.repo.upsert(salesperson_id, work_date, quantity, sp["name"])
        return self.repo.find_by_key(salesperson_id, work_date)

    def delete_record(self, id: int) -> bool:
        """판매 실적 레코드를 삭제한다."""
        return self.repo.delete(id)
