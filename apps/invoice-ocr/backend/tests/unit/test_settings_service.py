from unittest.mock import MagicMock

from app.services.settings_service import SettingsService
from tests.fixtures import settings_data as sd


def test_get_issuer_returns_data():
    repo = MagicMock()
    repo.find_issuer.return_value = {**sd.issuer(), "id": 1}
    result = SettingsService(repo).get_issuer()
    assert result["id"] == 1
    assert result["company_name"] == "성진모터스"


def test_get_issuer_returns_none():
    repo = MagicMock()
    repo.find_issuer.return_value = None
    assert SettingsService(repo).get_issuer() is None


def test_update_issuer_generates_tel_fax_both_phone_and_fax():
    data = sd.issuer({"phone": "02-1234", "fax": "02-5678"})
    repo = MagicMock()
    repo.find_issuer.return_value = {**data, "id": 1}
    SettingsService(repo).update_issuer(data)
    assert repo.upsert_issuer.call_args.args[0]["tel_fax"] == "02-1234/02-5678"


def test_update_issuer_generates_tel_fax_phone_only():
    data = sd.issuer({"phone": "02-1234"})
    del data["fax"]
    repo = MagicMock()
    repo.find_issuer.return_value = {**data, "id": 1}
    SettingsService(repo).update_issuer(data)
    assert repo.upsert_issuer.call_args.args[0]["tel_fax"] == "02-1234"


def test_update_issuer_generates_tel_fax_fax_only():
    data = sd.issuer({"fax": "02-5678"})
    del data["phone"]
    repo = MagicMock()
    repo.find_issuer.return_value = {**data, "id": 1}
    SettingsService(repo).update_issuer(data)
    assert repo.upsert_issuer.call_args.args[0]["tel_fax"] == "02-5678"


def test_update_issuer_generates_tel_fax_none_when_both_absent():
    data = sd.issuer()
    del data["phone"]
    del data["fax"]
    repo = MagicMock()
    repo.find_issuer.return_value = {**data, "id": 1}
    SettingsService(repo).update_issuer(data)
    assert repo.upsert_issuer.call_args.args[0]["tel_fax"] is None


def test_update_issuer_calls_find_issuer_after_upsert():
    data = sd.issuer({"phone": "02-1234", "fax": "02-5678"})
    stored = {**data, "id": 1, "tel_fax": "02-1234/02-5678"}
    repo = MagicMock()
    repo.upsert_issuer.return_value = 1
    repo.find_issuer.return_value = stored
    result = SettingsService(repo).update_issuer(data)
    assert result["tel_fax"] == "02-1234/02-5678"


def test_get_app_settings_returns_map():
    repo = MagicMock()
    repo.find_all_settings.return_value = sd.app_settings()
    result = SettingsService(repo).get_app_settings()
    assert result["default_vat_rate"] == "0.1"
    assert result["default_document_title"] == "거 래 명 세 서"


def test_upload_stamp_returns_none_when_no_issuer():
    repo = MagicMock()
    repo.find_issuer.return_value = None
    result = SettingsService(repo).upload_stamp(b"x", "image/png")
    assert result is None
    repo.update_stamp_url.assert_not_called()


def test_update_app_settings_calls_update_for_each_key():
    settings = {
        "default_vat_rate": "0.1",
        "default_document_title": "거 래 명 세 서",
        "default_unit": "EA",
    }
    repo = MagicMock()
    repo.find_all_settings.return_value = settings
    SettingsService(repo).update_app_settings(settings)
    assert repo.update_setting.call_count == 3


def test_update_app_settings_passes_correct_key_value_pairs():
    call_log = {}
    repo = MagicMock()
    repo.update_setting.side_effect = lambda k, v: call_log.__setitem__(k, v)
    repo.find_all_settings.return_value = {}
    SettingsService(repo).update_app_settings(
        {"default_vat_rate": "0.05", "default_unit": "BOX"}
    )
    assert call_log["default_vat_rate"] == "0.05"
    assert call_log["default_unit"] == "BOX"


def test_update_app_settings_ignores_unknown_keys():
    call_log = {}
    repo = MagicMock()
    repo.update_setting.side_effect = lambda k, v: call_log.__setitem__(k, v)
    repo.find_all_settings.return_value = {}
    SettingsService(repo).update_app_settings(
        {
            "default_vat_rate": "0.1",
            "evil_key": "hacked",
            "another_bad_key": "value",
        }
    )
    assert len(call_log) == 1
    assert "default_vat_rate" in call_log
    assert "evil_key" not in call_log
