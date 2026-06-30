import pytest
from sqlalchemy import text

from app.db import connection
from app.repositories.settings_repository import SettingsRepository
from tests.fixtures import settings_data as sd

pytestmark = pytest.mark.usefixtures("db_conn")


def test_find_issuer_returns_none_when_empty():
    assert SettingsRepository().find_issuer() is None


def test_upsert_issuer_inserts():
    repo = SettingsRepository()
    data = sd.issuer()
    new_id = repo.upsert_issuer(data)
    result = repo.find_issuer()
    assert isinstance(new_id, int) and new_id > 0
    assert result is not None
    assert result["company_name"] == data["company_name"]
    assert result["representative"] == data["representative"]
    assert result["business_number"] == data["business_number"]
    assert result["address"] == data["address"]
    assert result["phone"] == data["phone"]
    assert result["bank_account"] == data["bank_account"]
    assert int(result["show_sjdojang"]) == 1


def test_upsert_issuer_updates():
    repo = SettingsRepository()
    repo.upsert_issuer(sd.issuer({"company_name": "초기회사명", "representative": "초기대표자"}))
    repo.upsert_issuer(
        sd.issuer({"company_name": "변경된회사명", "representative": "변경된대표자"})
    )
    result = repo.find_issuer()
    assert result["company_name"] == "변경된회사명"
    assert result["representative"] == "변경된대표자"


def test_upsert_issuer_update_returns_same_id():
    repo = SettingsRepository()
    id1 = repo.upsert_issuer(sd.issuer({"company_name": "아이디확인회사"}))
    id2 = repo.upsert_issuer(sd.issuer({"company_name": "아이디확인회사변경"}))
    assert id1 == id2


def test_update_stamp_url():
    repo = SettingsRepository()
    issuer_id = repo.upsert_issuer(sd.issuer())
    url = "/uploads/stamps/test-stamp.png"
    repo.update_stamp_url(issuer_id, url)
    assert repo.find_issuer()["stamp_image_url"] == url


def test_update_stamp_url_overwrites_previous():
    repo = SettingsRepository()
    issuer_id = repo.upsert_issuer(sd.issuer())
    repo.update_stamp_url(issuer_id, "/uploads/old-stamp.png")
    repo.update_stamp_url(issuer_id, "/uploads/new-stamp.png")
    assert repo.find_issuer()["stamp_image_url"] == "/uploads/new-stamp.png"


def test_find_all_settings_returns_key_value_map():
    settings = SettingsRepository().find_all_settings()
    assert isinstance(settings, dict)
    assert settings["default_vat_rate"] == "0.1"
    assert settings["default_document_title"] == "거 래 명 세 서"
    assert settings["default_unit"] == "EA"
    assert settings["pdf_filename_pattern"] == "거래명세서_{recipient}_{issue_date}"


def test_update_setting():
    repo = SettingsRepository()
    repo.update_setting("default_unit", "BOX")
    assert repo.find_all_settings()["default_unit"] == "BOX"


def test_update_setting_does_not_affect_other_keys():
    repo = SettingsRepository()
    repo.update_setting("default_unit", "KG")
    settings = repo.find_all_settings()
    assert settings["default_vat_rate"] == "0.1"
    assert settings["default_document_title"] == "거 래 명 세 서"
    assert settings["default_unit"] == "KG"


def test_find_issuer_returns_latest():
    repo = SettingsRepository()
    with connection() as conn:
        conn.execute(
            text("""
            INSERT INTO issuers (company_name, representative, business_number, address)
            VALUES ('첫번째회사', '홍길동', '111-11-11111', '서울')
        """)
        )
        conn.execute(
            text("""
            INSERT INTO issuers (company_name, representative, business_number, address)
            VALUES ('두번째회사', '이순신', '222-22-22222', '부산')
        """)
        )
    result = repo.find_issuer()
    assert result["company_name"] == "두번째회사"
    assert result["representative"] == "이순신"
