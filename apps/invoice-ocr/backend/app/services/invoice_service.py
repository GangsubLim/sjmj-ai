"""InvoiceService — PHP services/InvoiceService.php 동형.

슬라이스 단계에서 company_repo/item_repo는 mock으로만 주입(골든 InvoiceServiceTest
동치). 실제 Company/Item 리포는 팬아웃에서 구현하며 increment_usage_by_name 규약만
약속한다.
"""

import math
from datetime import date

from app import db
from app.repositories.invoice_repository import InvoiceRepository


class InvoiceService:
    """거래명세서 비즈니스 로직과 트랜잭션 경계를 담당하는 서비스."""

    def __init__(self, repo=None, company_repo=None, item_repo=None, *, transaction=None):
        """리포지토리와 트랜잭션 컨텍스트를 주입해 서비스를 구성한다."""
        self.repo = repo or InvoiceRepository()
        self.company_repo = company_repo
        self.item_repo = item_repo
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        """필터 조건으로 거래명세서 목록과 페이지네이션 정보를 조회한다."""
        data = self.repo.find_all(filters)
        total = self.repo.count_all(filters)
        return {
            "data": data,
            "pagination": {
                "page": filters["page"],
                "limit": filters["limit"],
                "total": total,
                "totalPages": math.ceil(total / filters["limit"]) if filters["limit"] else 0,
            },
        }

    def get_by_id(self, id: int) -> dict | None:
        """거래명세서를 ID로 조회하고 품목을 함께 채워 반환한다(없으면 None)."""
        invoice = self.repo.find_by_id(id)
        if not invoice:
            return None
        invoice["items"] = self.repo.find_items(id)
        return invoice

    def create(self, data: dict) -> dict | None:
        """거래명세서와 품목을 한 트랜잭션으로 생성하고 usage_count를 갱신한다."""
        with self._transaction():
            invoice_id = self.repo.insert(data)
            for index, item in enumerate(data.get("items") or []):
                self.repo.insert_item({**item, "item_order": index + 1, "invoice_id": invoice_id})
            self._update_usage_count(data)
        return self.get_by_id(invoice_id)

    def update(self, id: int, data: dict) -> dict | None:
        """거래명세서를 수정하고 품목을 교체한다(없으면 None)."""
        if not self.repo.find_by_id(id):
            return None
        with self._transaction():
            self.repo.update(id, data)
            self.repo.delete_items(id)
            for index, item in enumerate(data.get("items") or []):
                self.repo.insert_item({**item, "item_order": index + 1, "invoice_id": id})
        return self.get_by_id(id)

    def delete(self, id: int) -> bool:
        """거래명세서를 삭제한다."""
        return self.repo.delete(id)

    def duplicate(self, id: int) -> dict | None:
        """거래명세서를 복제해 오늘 발행일로 새 명세서를 생성한다(없으면 None)."""
        original = self.get_by_id(id)
        if not original:
            return None
        new_data = {
            k: v for k, v in original.items() if k not in ("id", "created_at", "updated_at")
        }
        new_data["issue_date"] = date.today().isoformat()
        new_data["items"] = [
            {k: v for k, v in it.items() if k != "id"} for it in original.get("items", [])
        ]
        return self.create(new_data)

    def _update_usage_count(self, data: dict) -> None:
        if data.get("recipient") and self.company_repo:
            self.company_repo.increment_usage_by_name(data["recipient"])
        if self.item_repo:
            for item in data.get("items") or []:
                if item.get("name"):
                    self.item_repo.increment_usage_by_name(item["name"])
