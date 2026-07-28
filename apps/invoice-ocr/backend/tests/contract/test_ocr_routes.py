import io
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.repositories.ocr_repository import OcrRepository
from app.routers.ocr import _MAX_CROP_ROW
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


def _done_job(rows: list[dict] | None = None) -> int:
    repo = OcrRepository()
    job_id = repo.insert_job("/x.jpg")
    repo.update_result(
        job_id,
        "done",
        {"rows": rows or [], "supply_sum": 0, "warp_ok": True},
    )
    return job_id


@pytest.mark.parametrize(
    "source",
    [
        "top1_kept",
        "candidate_picked:0",
        "candidate_picked:4",
        "manual_picked",
        "manual_typed",
        "new_item_created",
    ],
)
def test_confirm_accepts_every_known_label_source(client, source):
    payload = td.invoice_with_items()
    payload["items"][0]["label_source"] = source
    assert client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload).status_code == 200


@pytest.mark.parametrize("source", ["candidate_picked:5", "candidate_picked", "chip", ""])
def test_confirm_rejects_unknown_label_source_with_400_envelope(client, source):
    payload = td.invoice_with_items()
    payload["items"][0]["label_source"] = source
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400  # 422가 아니다 — 외부 계약 불변식
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    details = b["error"]["details"]
    assert isinstance(details, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in details.items())
    assert "items.0.label_source" in details


def test_confirm_still_rejects_missing_required_fields_as_400(client):
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json={"recipient": "x"})
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert set(b["error"]["details"]) >= {"issue_date", "items"}


def test_confirm_rejects_blank_recipient(client):
    """공백뿐인 recipient는 전환 전 Validator.required와 동일하게 400이다."""
    payload = td.invoice_with_items({"recipient": "   "})
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400
    assert "recipient" in r.json()["error"]["details"]


def test_confirm_rejects_overlong_recipient(client):
    """invoices.recipient는 VARCHAR(100) — 초과분은 400이지 500이 아니다.

    이 payload는 invoices 라우터의 max_length(recipient, 100)를 우회해 repository로 직행하므로,
    상한이 없으면 MySQL ERROR 1406 → 미처리 예외 → 500 SERVER_ERROR + str(exc)(SQL문·파라미터
    노출)로 샌다.
    """
    payload = td.invoice_with_items({"recipient": "가" * 101})
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "recipient" in r.json()["error"]["details"]


def test_confirm_rejects_item_without_name(client):
    """name 없는 item은 400 — repository가 item['name']을 기본값 없이 인덱싱해 500이 된다."""
    payload = td.invoice_with_items()
    del payload["items"][1]["name"]
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "items.1.name" in r.json()["error"]["details"]


def test_confirm_rejects_impossible_calendar_date(client):
    payload = td.invoice_with_items({"issue_date": "2026-02-30"})
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400
    assert "issue_date" in r.json()["error"]["details"]


def test_confirm_rejects_empty_items(client):
    payload = td.invoice_with_items({"items": []})
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 400
    assert "items" in r.json()["error"]["details"]


def test_confirm_keeps_accepting_frontend_string_numerics(client, db_conn):
    """프론트는 입력 중 수량·단가를 문자열로 보낸다.

    invoice-item-row.tsx:100 → calculations.ts:53-54("원래 값 유지") → invoice-form.tsx:301.
    MySQL이 '12'를 12로 코어스하므로 전환 전에는 200이었다 — Pydantic이 조이면 운영 저장이
    깨진다. 회귀 가드.

    빈 문자열("")은 여기서 다루지 않는다: 전환 이전에도 STRICT_TRANS_TABLES에서
    ERROR 1366(Incorrect integer value)로 500이다(quantity·unit_price 양쪽 실측).
    선행 버그이며 별도 이슈로 분리한다.
    """
    payload = td.invoice_with_items()
    payload["items"][0]["quantity"] = "12"
    payload["items"][1]["unit_price"] = "50000"
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 200
    # 200만으로는 가드가 성립하지 않는다 — 모델이 두 필드를 정의 필드로 흡수하면서 값이
    # 유실되거나 기본값(0)으로 대체돼도 200이다. 저장된 정수값까지 확인한다.
    with db_conn.begin() as conn:
        stored = conn.execute(
            text(
                "SELECT quantity, unit_price FROM invoice_items "
                "WHERE invoice_id = :i ORDER BY item_order"
            ),
            {"i": r.json()["data"]["invoice_id"]},
        ).fetchall()
    assert stored[0][0] == 12
    assert stored[1][1] == 50000


def test_confirm_with_ocr_only_keys_still_saves_invoice_items(client, db_conn):
    """crop_ref·label_source가 실린 payload도 invoice_items를 정상 저장한다(전 구간 회귀).

    strip 자체는 여기서 고정되지 않는다 — InvoiceRepository.insert_item이 명시 바인드
    파라미터만 쓰므로 strip을 지워도 SQL·저장 결과가 같다. 그 불변식은 관측 가능한 seam에서
    tests/unit/test_ocr_service.py::test_confirm_strips_ocr_only_keys_at_the_invoice_service_seam
    가 고정한다. 이 테스트가 덮는 것은 '라우터→서비스 경유로 item 3건이 순서대로 저장된다'다.
    """
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = "job-1/row-0"
    payload["items"][0]["label_source"] = "manual_typed"
    r = client.post(f"/api/ocr/jobs/{_done_job()}/confirm", json=payload)
    assert r.status_code == 200
    with db_conn.begin() as conn:
        names = [
            row[0]
            for row in conn.execute(
                text("SELECT name FROM invoice_items WHERE invoice_id = :i ORDER BY item_order"),
                {"i": r.json()["data"]["invoice_id"]},
            )
        ]
    assert names == ["엔진오일", "브레이크오일", "에어필터"]


def test_confirm_persists_label_source_into_correction_json(client, db_conn):
    """핵심 payoff의 end-to-end 증명 — 라우터(Pydantic) → 서비스 strip → build_correction → DB.

    tests/integration/test_ocr_service.py의 confirm 테스트는 OcrService를 직접 호출해
    라우터 층을 우회하므로, 이 계약 테스트가 전 구간을 덮는 유일한 단언이다.
    """
    job_id = _done_job(
        [
            {
                "row_index": 0,
                "crop_ref": "job-X/row-0",
                "item_top5": [{"label": "타이어", "sim": 0.72}],
                "supply": 100000,
            }
        ]
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = "job-X/row-0"
    payload["items"][0]["label_source"] = "candidate_picked:2"

    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r.status_code == 200

    with db_conn.begin() as conn:
        raw = conn.execute(
            text("SELECT correction_json FROM ocr_corrections WHERE job_id = :j"),
            {"j": job_id},
        ).scalar()
    correction = raw if isinstance(raw, dict) else json.loads(raw)
    assert correction["lines"][0]["label_source"] == "candidate_picked:2"


def test_confirm_typo_label_source_key_is_silently_dropped(client, db_conn):
    """`extra="allow"`가 수용한 위험을 계약으로 고정한다 — 버그를 정상화하는 게 아니다.

    오타 키(`label_soruce`)는 OcrConfirmItem에 정의된 필드가 아니므로 extra="allow"를 타고
    조용히 통과한다(200). `label_source`는 미전송 취급되어 correction_json에 null로 남는다
    — 오타 방어선(Task 12, 프론트 `attachLabelSource` 고정)이 도입되기 전까지 이 대가는
    ocr.py:58-61 주석대로 수용된 리스크다.
    """
    job_id = _done_job(
        [
            {
                "row_index": 0,
                "crop_ref": "job-Z/row-0",
                "item_top5": [{"label": "타이어", "sim": 0.72}],
                "supply": 100000,
            }
        ]
    )
    payload = td.invoice_with_items()
    payload["items"][0]["crop_ref"] = "job-Z/row-0"
    payload["items"][0]["label_soruce"] = "candidate_picked:2"  # 오타 키 — 의도적

    r = client.post(f"/api/ocr/jobs/{job_id}/confirm", json=payload)
    assert r.status_code == 200

    with db_conn.begin() as conn:
        raw = conn.execute(
            text("SELECT correction_json FROM ocr_corrections WHERE job_id = :j"),
            {"j": job_id},
        ).scalar()
    correction = raw if isinstance(raw, dict) else json.loads(raw)
    assert correction["lines"][0]["label_source"] is None


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


# ── GET /api/ocr/jobs/{id}/crop/{row} ──────────────────────────────────────
# tests/contract/test_curation_routes.py:319-334, 396-414에서 이관·개작
# (crop은 확정 전에도 필요해 /curation이 아닌 /ocr 네임스페이스에 둔다).

_CROP_PNG_BYTES = b"\x89PNG\r\n\x1a\n"


def test_crop_image_returns_png(client, tmp_path):
    job_id = _done_job()
    crop_dir = tmp_path / "ocr_crops" / f"job-{job_id}"
    crop_dir.mkdir(parents=True)
    (crop_dir / "row-0.png").write_bytes(_CROP_PNG_BYTES)
    res = client.get(f"/api/ocr/jobs/{job_id}/crop/0")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == _CROP_PNG_BYTES


def test_crop_image_404_when_file_missing(client, tmp_path):
    res = client.get(f"/api/ocr/jobs/{_done_job()}/crop/0")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_crop_404_when_job_missing(client):
    res = client.get("/api/ocr/jobs/999999/crop/0")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_crop_blocks_path_traversal_via_row(client):
    """SJMJ_DATA_DIR(=tmp_path) 밖에 민감 파일을 두고 row로 도달 불가함을 실증한다.

    tests/contract/test_curation_routes.py:404-417에서 토큰·단언 그대로 이관(보안 회귀 방지).
    row는 int path 파라미터라 단일 세그먼트 조작 토큰은 422→400 변환기가 거부한다.
    (%2e%2e는 서버에서 ".."로 디코드되지만 단일 세그먼트라 int 파싱에서 걸린다.)

    세 토큰 모두 int 파싱에서 걸려 경로 조립에 도달하지 않으므로, 실제 경로 조립(절대성 +
    레이아웃)은 tests/unit/test_ocr_service.py::test_crop_image_uses_job_exists_not_find_job
    가 조립 경로 전체를 동등 비교로 고정한다.
    """
    job_id = _done_job()

    for evil in ("%2e%2e", "row-0.png", "..%2e"):
        res = client.get(f"/api/ocr/jobs/{job_id}/crop/{evil}")
        assert res.status_code == 400, f"traversal token not rejected: {evil!r}"
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def _all_route_paths(app) -> list[str]:
    """app.routes를 재귀적으로 펼쳐 모든 라우트 path를 모은다.

    FastAPI 0.138(starlette 1.3.1)의 include_router는 최상위 app.routes에
    `_IncludedRouter`(path 속성 없음)만 노출하고 실제 라우트는
    `original_router.routes`에 중첩된다 — 단순 `app.routes` 순회로는
    하위 라우트를 못 찾아 부정 단언이 항상 공허하게 참이 된다.
    """
    paths: list[str] = []
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(path)
        nested = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None
        )
        if nested:
            stack.extend(nested)
    return paths


def test_old_curation_crop_route_is_gone():
    """status code로는 판별할 수 없다 — frontend/dist가 있으면 SPA catch-all
    (app/main.py:42-48)이 미매칭 GET에 200 index.html을 준다. 라우트 테이블을 직접 본다."""
    from app.main import app

    paths = _all_route_paths(app)
    # positive control: 수집기 자체가 깨지면 아래 부정 단언이 공허하게 참이 되므로,
    # 신 경로가 실제로 수집됨을 먼저 확인해 수집기 정상 동작을 실증한다.
    assert "/ocr/jobs/{id}/crop/{row}" in paths
    assert not any(path.endswith("/curation/jobs/{job_id}/crop/{row}") for path in paths)


def test_crop_rejects_absurdly_large_row_without_500_or_path_leak(client, tmp_path):
    """row 상한 부재로 파일명 초과 OSError → 500 + 절대경로 노출을 재현·회귀 방지한다.

    (품질 리뷰 재현) huge row는 예전엔 filesystem 계층까지 도달해 OSError가 나고
    전역 핸들러(app/core/errors.py:_unhandled_handler)가 str(exc)를 그대로 실어
    SJMJ_DATA_DIR 절대경로가 응답에 노출됐다. 400 VALIDATION_ERROR로 흡수해야 한다.
    """
    job_id = _done_job()
    huge_row = "9" * 300
    res = client.get(f"/api/ocr/jobs/{job_id}/crop/{huge_row}")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert str(tmp_path) not in res.text


def test_crop_rejects_negative_row(client):
    job_id = _done_job()
    res = client.get(f"/api/ocr/jobs/{job_id}/crop/-1")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_crop_row_upper_bound_is_inclusive_at_max(client):
    """상한 경계 고정 — _MAX_CROP_ROW(9999)는 통과하고 그다음 값부터 400이다.

    커버가 -1과 300자리 숫자뿐이면 le를 le=5로 좁혀도 둘 다 통과한다 — 수십 행짜리 실제
    명세서의 정상 요청이 400으로 죽는 회귀를 계약이 못 잡는다.
    """
    job_id = _done_job()
    ok = client.get(f"/api/ocr/jobs/{job_id}/crop/{_MAX_CROP_ROW}")
    assert ok.status_code == 404  # 검증 통과 → 파일 부재로 404
    assert ok.json()["error"]["code"] == "NOT_FOUND"

    over = client.get(f"/api/ocr/jobs/{job_id}/crop/{_MAX_CROP_ROW + 1}")
    assert over.status_code == 400
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"
