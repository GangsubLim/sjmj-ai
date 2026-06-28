import pytest

from tests.fixtures import settings_data as sd

pytestmark = pytest.mark.usefixtures("db_conn")


def _put_issuer(client, **ov):
    return client.put("/api/settings/issuer", json=sd.issuer(ov))


# ------------------------------------------------------------------ #
# getIssuer
# ------------------------------------------------------------------ #


def test_get_issuer_returns_404_when_empty(client):
    r = client.get("/api/settings/issuer")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_get_issuer_returns_data(client):
    _put_issuer(client)
    r = client.get("/api/settings/issuer")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["company_name"] == "성진모터스"


# ------------------------------------------------------------------ #
# updateIssuer
# ------------------------------------------------------------------ #


def test_update_issuer_returns_data(client):
    r = _put_issuer(client)
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["company_name"] == "성진모터스"
    assert b["data"]["tel_fax"] == "02-1234-5678/02-1234-5679"


def test_update_issuer_upsert_updates_existing(client):
    _put_issuer(client, company_name="초기회사명")
    r = _put_issuer(client, company_name="변경된회사명")
    assert r.json()["data"]["company_name"] == "변경된회사명"
    # 단일 발급자만 존재해야 한다(upsert)
    assert (
        client.get("/api/settings/issuer").json()["data"]["company_name"]
        == "변경된회사명"
    )


def test_update_issuer_fails_validation_missing_company_name(client):
    body = sd.issuer()
    del body["company_name"]
    r = client.put("/api/settings/issuer", json=body)
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "company_name" in b["error"]["details"]


def test_update_issuer_fails_invalid_business_number(client):
    r = _put_issuer(client, business_number="123456789")  # 9자리 → 무효
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ #
# uploadStamp
# ------------------------------------------------------------------ #


def test_upload_stamp_returns_data(client):
    _put_issuer(client)
    r = client.post(
        "/api/settings/issuer/stamp",
        files={"image": ("stamp.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["stamp_image_url"].startswith("/uploads/stamps/stamp_")
    # 부수효과: issuers.stamp_image_url 갱신
    assert (
        client.get("/api/settings/issuer").json()["data"]["stamp_image_url"]
        == b["data"]["stamp_image_url"]
    )


def test_upload_stamp_fails_no_file(client):
    _put_issuer(client)
    r = client.post("/api/settings/issuer/stamp")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_stamp_returns_404_when_no_issuer(client):
    r = client.post(
        "/api/settings/issuer/stamp",
        files={"image": ("stamp.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_upload_stamp_rejects_wrong_type(client):
    _put_issuer(client)
    r = client.post(
        "/api/settings/issuer/stamp",
        files={"image": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_stamp_rejects_oversize(client):
    _put_issuer(client)
    big = b"\x89" * (500 * 1024 + 1)
    r = client.post(
        "/api/settings/issuer/stamp",
        files={"image": ("stamp.png", big, "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ #
# getAppSettings / updateAppSettings
# ------------------------------------------------------------------ #


def test_get_app_settings_returns_map(client):
    r = client.get("/api/settings/app")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["default_vat_rate"] == "0.1"
    assert b["data"]["default_document_title"] == "거 래 명 세 서"


def test_update_app_settings_returns_data(client):
    r = client.put("/api/settings/app", json={"default_vat_rate": "0.05"})
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["default_vat_rate"] == "0.05"


def test_update_app_settings_ignores_unknown_keys(client):
    r = client.put(
        "/api/settings/app", json={"default_unit": "BOX", "evil_key": "hacked"}
    )
    b = r.json()
    assert b["data"]["default_unit"] == "BOX"
    assert "evil_key" not in b["data"]


def test_update_app_settings_fails_empty_body(client):
    r = client.put("/api/settings/app", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
