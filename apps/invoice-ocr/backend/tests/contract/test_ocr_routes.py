import io
from pathlib import Path

import pytest
from sqlalchemy import text

from app.repositories.ocr_repository import OcrRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))


def test_create_job_accepts_multipart_and_returns_201(client):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("scan.jpg", io.BytesIO(b"\xff\xd8\xff x"), "image/jpeg")},
    )
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["status"] == "pending"
    assert isinstance(b["data"]["job_id"], int)


def test_create_job_rejects_disallowed_suffix(client):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("x.sh", io.BytesIO(b"#!/bin/sh"), "application/octet-stream")},
    )
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert b["error"]["message"] == "jpg/jpeg/png 형식만 업로드할 수 있습니다."
    assert b["error"]["details"] == {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."}


def test_create_job_rejects_missing_suffix(client):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("photo", io.BytesIO(b"\xff\xd8\xff x"), "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"] == {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."}


def test_create_job_rejects_control_char_filename(client):
    # httpx multipart는 C0 제어문자 대부분을 percent-encode(\n→%0A, \x00→%00)해 서버에 raw로
    # 도달하지 않지만, U+001B(ESC)는 예외로 남아 raw로 통과한다(httpx/_multipart.py의
    # _HTML5_FORM_ENCODING_REPLACEMENTS가 `range(0x1F+1) if c != 0x1B`로 구성됨 — 실측 확인).
    # 계약은 raw 통과가 확인된 U+007F(DEL)로 고정한다(U+001B 포함 나머지 C0는 unit 테스트 담당).
    # 실측 기준: httpx 0.28.1 / starlette 1.3.1 (2026-07-27). starlette가
    # StarletteDeprecationWarning으로 httpx2를 권고 중이므로, httpx2 전환 후 이 테스트가 201로
    # 실패하면 인코딩 동작이 바뀐 것이다 — 재실측 후 U+007F 케이스를 unit 테스트로 이관할 것.
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("x\x7f.jpg", io.BytesIO(b"\xff\xd8\xff x"), "image/jpeg")},
    )
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["message"] == "파일명에 허용되지 않는 문자가 포함되어 있습니다."
    assert b["error"]["details"] == {"photo": "파일명에 제어문자를 사용할 수 없습니다."}


def test_create_job_normalizes_uppercase_suffix_and_keeps_path_shape(client, tmp_path):
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("SCAN.PNG", io.BytesIO(b"\x89PNG x"), "image/png")},
    )
    assert r.status_code == 201
    stored = Path(OcrRepository().find_job(r.json()["data"]["job_id"])["image_path"])
    # 저장 위치가 $SJMJ_DATA_DIR/ocr_uploads임을 고정 — 아래 거부 테스트의 부정 단언
    # (not (tmp_path/"ocr_uploads").exists())이 경로 변경으로 공허해지는 것을 막는다.
    assert stored.parent == tmp_path / "ocr_uploads"
    assert stored.suffix == ".png"  # 대문자 확장자가 소문자로 정규화됨
    # 저장 파일명 형태 불변: uuid4().hex(32자 소문자 hex) + suffix — ml-worker 경로 회귀 방지
    assert len(stored.stem) == 32
    assert all(c in "0123456789abcdef" for c in stored.stem)


def test_rejected_upload_leaves_no_file_and_no_db_row(client, tmp_path, db_conn):
    """거부된 업로드는 디스크·DB에 부수효과를 남기지 않는다(검증이 write/insert보다 앞선다).

    이 작업의 핵심 보안 불변식. 검증이 미래 리팩터로 write 뒤로 밀리면 임의 확장자 파일이
    디스크에 남는데, 그 회귀를 이 테스트가 잡는다.
    """
    r = client.post(
        "/api/ocr/jobs",
        files={"photo": ("x.sh", io.BytesIO(b"#!/bin/sh"), "application/octet-stream")},
    )
    assert r.status_code == 400
    # _upload_root()가 mkdir까지 하므로 디렉토리 자체가 없어야 한다(= 호출조차 되지 않았다)
    assert not (tmp_path / "ocr_uploads").exists()
    with db_conn.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM ocr_jobs")).scalar() == 0


def test_get_job_returns_done_with_result(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.get(f"/api/ocr/jobs/{job_id}")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert b["data"]["status"] == "done"
    assert b["data"]["result"]["warp_ok"] is True


def test_get_job_404(client):
    r = client.get("/api/ocr/jobs/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_confirm_creates_invoice(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r.status_code == 200
    assert r.json()["data"]["invoice_id"] > 0


def test_confirm_validation_error(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json={"recipient": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_confirm_twice_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(job_id, "done", {"rows": [], "supply_sum": 0, "warp_ok": True})
    payload = td.invoice_with_items()
    assert client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload).status_code == 200
    r2 = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"


def test_confirm_pending_job_returns_409(client):
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")  # status=pending, result_json 없음
    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=td.invoice_with_items())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"
