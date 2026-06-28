"""CompanyService — PHP services/CompanyService.php 동형.

가짜 pagination(page=1, limit=9999, total=count, totalPages=1)을 그대로 유지한다.
company 단건은 트랜잭션이 필요 없는 단일 statement지만, 슬라이스 패턴과의
일관성을 위해 transaction seam은 주입 가능하게 둔다(미사용).
"""

from app import db
from app.repositories.companies_repository import CompanyRepository

_FAKE_LIMIT = 9999


class CompanyService:
    def __init__(self, repo=None, *, transaction=None):
        self.repo = repo or CompanyRepository()
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        data = self.repo.find_all(filters)
        total = len(data)
        return {
            "data": data,
            "pagination": {
                "page": 1,
                "limit": _FAKE_LIMIT,
                "total": total,
                "totalPages": 1,
            },
        }

    def get_by_id(self, id: int) -> dict | None:
        return self.repo.find_by_id(id)

    def create(self, data: dict) -> dict:
        new_id = self.repo.insert(data)
        return self.repo.find_by_id(new_id) or data

    def update(self, id: int, data: dict) -> dict | None:
        if not self.repo.find_by_id(id):
            return None
        self.repo.update(id, data)
        return self.repo.find_by_id(id)

    def delete(self, id: int) -> bool:
        return self.repo.delete(id)

    def get_invoices(self, id: int) -> list[dict] | None:
        if not self.repo.find_by_id(id):
            return None
        return self.repo.find_invoices_by_company_id(id)
