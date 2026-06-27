"""SettingsRepository — PHP repositories/SettingsRepository.php 동형(text() raw SQL).

issuers(단일 발급자 upsert)와 app_settings(키-값 맵)를 다룬다.
"""
from sqlalchemy import text

from app.db import connection

_ISSUER_COLUMNS = """
    id, company_name, representative, business_number, address,
    business_type, business_item, phone, fax, tel_fax,
    bank_account, stamp_image_url, show_sjdojang,
    created_at, updated_at
"""


class SettingsRepository:
    def find_issuer(self) -> dict | None:
        sql = f"SELECT {_ISSUER_COLUMNS} FROM issuers ORDER BY id DESC LIMIT 1"
        with connection() as conn:
            row = conn.execute(text(sql)).mappings().first()
            return dict(row) if row else None

    def upsert_issuer(self, data: dict) -> int:
        params = {
            "company_name": data["company_name"],
            "representative": data["representative"],
            "business_number": data["business_number"],
            "address": data["address"],
            "business_type": data.get("business_type"),
            "business_item": data.get("business_item"),
            "phone": data.get("phone"),
            "fax": data.get("fax"),
            "tel_fax": data.get("tel_fax"),
            "bank_account": data.get("bank_account"),
            "show_sjdojang": 1 if data.get("show_sjdojang", 1) else 0,
        }
        existing = self.find_issuer()
        with connection() as conn:
            if existing:
                conn.execute(text("""
                    UPDATE issuers SET
                        company_name = :company_name,
                        representative = :representative,
                        business_number = :business_number,
                        address = :address,
                        business_type = :business_type,
                        business_item = :business_item,
                        phone = :phone,
                        fax = :fax,
                        tel_fax = :tel_fax,
                        bank_account = :bank_account,
                        show_sjdojang = :show_sjdojang
                    WHERE id = :id
                """), {**params, "id": existing["id"]})
                return int(existing["id"])
            result = conn.execute(text("""
                INSERT INTO issuers (
                    company_name, representative, business_number, address,
                    business_type, business_item, phone, fax, tel_fax,
                    bank_account, show_sjdojang
                ) VALUES (
                    :company_name, :representative, :business_number, :address,
                    :business_type, :business_item, :phone, :fax, :tel_fax,
                    :bank_account, :show_sjdojang
                )
            """), params)
            return int(result.lastrowid)

    def update_stamp_url(self, issuer_id: int, url: str) -> None:
        with connection() as conn:
            conn.execute(
                text("UPDATE issuers SET stamp_image_url = :url WHERE id = :id"),
                {"url": url, "id": issuer_id},
            )

    def find_all_settings(self) -> dict:
        with connection() as conn:
            rows = conn.execute(
                text("SELECT setting_key, setting_value FROM app_settings")
            ).mappings().all()
            return {r["setting_key"]: r["setting_value"] for r in rows}

    def update_setting(self, key: str, value: str) -> None:
        with connection() as conn:
            conn.execute(
                text("UPDATE app_settings SET setting_value = :value WHERE setting_key = :key"),
                {"value": value, "key": key},
            )
