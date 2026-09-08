"""hermes 슬라이스 계약 테스트 — envelope shape + spec §4.2 부재·오류 계약.

데이터 루트를 tmp_path로 갈아끼운다. config.data_root()가 매 호출 os.environ을 다시 읽는
설계라 성립한다(get_settings의 @lru_cache를 경유하지 않는다).
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_conn")


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """SJMJ_DATA_DIR을 임시 루트로 바꾸고 agent_uploads를 만들어 그 경로를 준다."""
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    d = tmp_path / "agent_uploads"
    d.mkdir()
    return d


@pytest.fixture
def data_root_only(tmp_path, monkeypatch):
    """agent_uploads를 만들지 않은 데이터 루트 — 디렉터리 부재 계약용."""
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    return tmp_path


def _draft(**over) -> dict:
    base = {
        "issue_date": "2026-09-05",
        "recipient": "○○상사",
        "grand_total": 165000,
        "items": [
            {
                "name": "각파이프 50x50",
                "quantity": 10,
                "unit": "EA",
                "unit_price": 15000,
                "supply": 150000,
                "deduction": False,
            }
        ],
    }
    base.update(over)
    return base


def _write_draft(uploads, invoice_id: int, body: dict | None = None) -> None:
    (uploads / f"{invoice_id}.draft.json").write_text(
        json.dumps(body if body is not None else _draft(), ensure_ascii=False), encoding="utf-8"
    )


def _seed_invoice(engine, *, recipient="○○상사", grand_total=165000, items=None) -> int:
    """invoices 1건 + invoice_items N건 시드. 생성된 invoice id 반환."""
    items = items if items is not None else [("각파이프 50x50", 150000)]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO invoices (issue_date, recipient, grand_total) "
                "VALUES ('2026-09-05', :r, :g)"
            ),
            {"r": recipient, "g": grand_total},
        )
        invoice_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        for order, (name, supply) in enumerate(items):
            conn.execute(
                text(
                    "INSERT INTO invoice_items "
                    "(invoice_id, item_order, name, quantity, unit, unit_price, supply, deduction) "
                    "VALUES (:i, :o, :n, 10, 'EA', 15000, :s, 0)"
                ),
                {"i": invoice_id, "o": order, "n": name, "s": supply},
            )
    return invoice_id


# --- summary envelope ---


def test_summary_returns_success_envelope(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    res = client.get("/api/hermes/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data) == {"totals", "knowledge", "by_version"}
    assert data["totals"]["count"] == 1
    assert data["totals"]["untouched"] == 1


def test_summary_omits_edited_metric(client, uploads, db_conn):
    """edited는 타임스탬프 파생이라 응답에 실리지 않는다(spec §3.2)."""
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    data = client.get("/api/hermes/summary").json()["data"]
    assert "edited" not in data["totals"]
    assert all("edited" not in row for row in data["by_version"])


def test_summary_counts_deleted_draft_as_missing(client, uploads, db_conn):
    """DB에 최종본이 없는 초안은 missing으로 센다(집계 분모에는 들어가지 않는다)."""
    _write_draft(uploads, 999_001)
    data = client.get("/api/hermes/summary").json()["data"]
    assert data["totals"]["count"] == 0
    assert data["totals"]["missing"] == 1


# --- §4.2 빈 200 ---


def test_summary_empty_when_uploads_dir_absent(client, data_root_only):
    res = client.get("/api/hermes/summary")
    assert res.status_code == 200
    assert res.json()["data"]["totals"]["count"] == 0
    # 읽기 전용 원칙 — 조회가 디렉터리를 만들지 않는다.
    assert not (data_root_only / "agent_uploads").exists()


def test_summary_empty_when_no_drafts(client, uploads):
    assert client.get("/api/hermes/summary").json()["data"]["totals"]["count"] == 0


def test_summary_knowledge_defaults_when_files_absent(client, uploads):
    """versions.jsonl·corrections.jsonl 부재는 200 + 기본값(agent_learn과 같은 취급)."""
    knowledge = client.get("/api/hermes/summary").json()["data"]["knowledge"]
    assert knowledge == {"version": 0, "published_at": None, "corrections": 0}


def test_summary_knowledge_reads_versions_and_corrections(client, uploads, tmp_path):
    kdir = tmp_path / "agent_knowledge"
    kdir.mkdir()
    (kdir / "versions.jsonl").write_text(
        json.dumps({"version": 1, "published_at": "2026-09-01T00:00:00", "corrections_through": 5})
        + "\n"
        + json.dumps({"version": 1, "published_at": "2026-09-02T00:00:00", "rejected": "금지어"})
        + "\n"
        + json.dumps(
            {"version": 2, "published_at": "2026-09-03T00:00:00", "corrections_through": 25}
        )
        + "\n",
        encoding="utf-8",
    )
    (kdir / "corrections.jsonl").write_text("{}\n{}\n{}\n", encoding="utf-8")
    knowledge = client.get("/api/hermes/summary").json()["data"]["knowledge"]
    # 거부 기록은 현재 버전을 승격시키지 않는다.
    assert knowledge == {"version": 2, "published_at": "2026-09-03T00:00:00", "corrections": 3}
    # 읽기 전용 — agent_knowledge를 만들지도, 건드리지도 않는다.
    assert sorted(p.name for p in kdir.iterdir()) == ["corrections.jsonl", "versions.jsonl"]


def test_summary_does_not_create_knowledge_dir(client, uploads, tmp_path):
    client.get("/api/hermes/summary")
    assert not (tmp_path / "agent_knowledge").exists()


# --- 버전 축 ---


def test_summary_groups_by_knowledge_version(client, uploads, db_conn, tmp_path):
    """최종본 created_at을 발행 구간에 매핑한다. 발행 이전은 version 0."""
    kdir = tmp_path / "agent_knowledge"
    kdir.mkdir()
    (kdir / "versions.jsonl").write_text(
        json.dumps({"version": 1, "published_at": "2030-01-01T00:00:00", "corrections_through": 1})
        + "\n",
        encoding="utf-8",
    )
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    by_version = client.get("/api/hermes/summary").json()["data"]["by_version"]
    # 발행 시각이 미래라 이 건은 어느 버전에도 안 걸린다 → 0.
    assert [row["version"] for row in by_version] == [0]
    assert by_version[0]["count"] == 1


# --- §4.2 500 ---


def test_summary_500_when_draft_json_is_corrupt(client, uploads):
    (uploads / "42.draft.json").write_text("{ not json", encoding="utf-8")
    res = client.get("/api/hermes/summary")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SERVER_ERROR"


def test_summary_500_when_data_dir_unset(db_conn, monkeypatch):
    """이 건만 로컬 TestClient(raise_server_exceptions=False)를 쓴다.

    data_root() 미설정 시 RuntimeError가 앱의 전역 `Exception` 핸들러(500)까지 가는데,
    Starlette의 ServerErrorMiddleware는 그 핸들러 응답을 보낸 뒤에도 예외를 항상
    재-raise한다(starlette/middleware/errors.py: "We always continue to raise the
    exception"). 기본 conftest `client`(raise_server_exceptions=True)는 이걸 그대로
    다시 던져 테스트가 500 응답을 못 보고 예외로 죽는다 — AppError로 명시적으로 던지는
    다른 500 케이스(예: 위 draft.json 손상)는 이 경로를 안 타 영향이 없다. 이 완화를
    파일 전체 `client` fixture로 올리면 아직 안 쓰인 뒤 태스크(목록·상세·사진)의 미처리
    예외까지 조용히 삼켜 안전망이 낮아지므로, 이 건 안에서만 국소적으로 적용한다.
    """
    monkeypatch.delenv("SJMJ_DATA_DIR", raising=False)
    from app.main import app

    res = TestClient(app, raise_server_exceptions=False).get("/api/hermes/summary")
    assert res.status_code == 500
    assert res.json()["success"] is False


# --- entries 목록 ---


def test_entries_returns_list_envelope_with_pagination(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    res = client.get("/api/hermes/entries")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["pagination"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}
    entry = body["data"][0]
    assert entry["id"] == invoice_id
    assert entry["status"] == "match"
    assert entry["mismatch_fields"] == []
    assert entry["recipient_draft"] == "○○상사"
    assert entry["recipient_final"] == "○○상사"
    assert entry["item_count_draft"] == 1
    assert entry["item_count_final"] == 1
    assert entry["grand_total_draft"] == 165000
    assert entry["grand_total_final"] == 165000
    assert entry["issue_date_draft"] == "2026-09-05"
    assert entry["issue_date_final"] == "2026-09-05"
    assert entry["has_photo"] is False
    assert entry["has_raw"] is False


def test_entries_deleted_entry_has_null_finals(client, uploads):
    _write_draft(uploads, 999_002)
    entry = client.get("/api/hermes/entries").json()["data"][0]
    assert entry["status"] == "deleted"
    assert entry["recipient_final"] is None
    assert entry["issue_date_final"] is None
    assert entry["item_count_final"] is None
    assert entry["grand_total_final"] is None
    assert entry["mismatch_fields"] == []


def test_entries_mismatch_fields_are_folded_axes(client, uploads, db_conn):
    """품목명만 고친 건 — 부모 타임스탬프 무변경이어도 mismatch로 잡힌다."""
    invoice_id = _seed_invoice(db_conn, items=[("히터", 150000)])
    _write_draft(uploads, invoice_id, _draft(items=[{"name": "히타", "supply": 150000}]))
    entry = client.get("/api/hermes/entries").json()["data"][0]
    assert entry["status"] == "mismatch"
    assert entry["mismatch_fields"] == ["name"]


def test_entries_reports_photo_and_raw_presence(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    (uploads / f"{invoice_id}.jpg").write_bytes(b"\xff\xd8\xff")
    (uploads / f"{invoice_id}.raw.json").write_text(
        json.dumps({"rows": [{"raw": "히타", "conf": "중"}]}), encoding="utf-8"
    )
    entry = client.get("/api/hermes/entries").json()["data"][0]
    assert entry["has_photo"] is True
    assert entry["has_raw"] is True


def test_entries_sorted_by_id_desc(client, uploads, db_conn):
    """최신 건이 먼저 — 방금 텔레그램으로 보낸 건을 맨 위에서 본다."""
    first = _seed_invoice(db_conn)
    second = _seed_invoice(db_conn)
    _write_draft(uploads, first)
    _write_draft(uploads, second)
    ids = [e["id"] for e in client.get("/api/hermes/entries").json()["data"]]
    assert ids == [second, first]


def test_entries_status_filter_narrows_list_and_total(client, uploads, db_conn):
    """목록과 pagination.total이 같은 조건으로 좁혀진다(curation row_delta와 같은 계약)."""
    matched = _seed_invoice(db_conn)
    _write_draft(uploads, matched)
    _write_draft(uploads, 999_003)  # 최종본 없음 → deleted
    body = client.get("/api/hermes/entries", params={"status": "deleted"}).json()
    assert body["pagination"]["total"] == 1
    assert [e["id"] for e in body["data"]] == [999_003]


def test_entries_rejects_unknown_status_with_400(client, uploads):
    res = client.get("/api/hermes/entries", params={"status": "unknown"})
    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "status" in body["error"]["details"]


def test_entries_clamps_page_and_limit(client, uploads):
    body = client.get("/api/hermes/entries", params={"page": 0, "limit": 9999}).json()
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 100


def test_entries_empty_when_uploads_dir_absent(client, data_root_only):
    res = client.get("/api/hermes/entries")
    assert res.status_code == 200
    assert res.json()["data"] == []
    assert res.json()["pagination"]["total"] == 0
    assert not (data_root_only / "agent_uploads").exists()


# --- 대조 상세 ---


def test_entry_detail_returns_three_way_rows(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn, items=[("히터", 150000)])
    _write_draft(
        uploads,
        invoice_id,
        _draft(
            items=[
                {
                    "name": "히타",
                    "quantity": 10,
                    "unit": "EA",
                    "unit_price": 15000,
                    "supply": 150000,
                    "deduction": False,
                }
            ]
        ),
    )
    (uploads / f"{invoice_id}.raw.json").write_text(
        json.dumps({"rows": [{"raw": "히타", "conf": "중"}]}, ensure_ascii=False), encoding="utf-8"
    )
    res = client.get(f"/api/hermes/entries/{invoice_id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["id"] == invoice_id
    assert data["status"] == "mismatch"
    assert data["final"]["recipient"] == "○○상사"
    assert data["has_photo"] is False
    row = data["rows"][0]
    assert row["index"] == 0
    assert row["raw"] == {"text": "히타", "conf": "중"}
    assert row["draft"]["name"] == "히타"
    assert row["draft"]["unit_price"] == 15000
    assert row["final"]["name"] == "히터"
    assert row["mismatch"] == ["name"]


def test_entry_detail_raw_is_null_when_file_absent(client, uploads, db_conn):
    """raw.json 부재는 200 + 해당 행 raw = null(spec §4.2)."""
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    data = client.get(f"/api/hermes/entries/{invoice_id}").json()["data"]
    assert data["rows"][0]["raw"] is None


def test_entry_detail_deleted_has_null_final_and_rows(client, uploads):
    _write_draft(uploads, 999_004)
    data = client.get("/api/hermes/entries/999004").json()["data"]
    assert data["status"] == "deleted"
    assert data["final"] is None
    assert data["rows"][0]["final"] is None
    assert data["rows"][0]["mismatch"] == []


def test_entry_detail_keeps_rows_the_human_added(client, uploads, db_conn):
    """사람이 더한 행은 draft=null로 남긴다 — 여기서 잘라내면 항목 수 불일치의 실물이 사라진다."""
    invoice_id = _seed_invoice(db_conn, items=[("각파이프 50x50", 150000), ("용접봉", 60000)])
    _write_draft(uploads, invoice_id)  # 초안은 1행
    data = client.get(f"/api/hermes/entries/{invoice_id}").json()["data"]
    assert len(data["rows"]) == 2
    assert data["rows"][1]["draft"] is None
    assert data["rows"][1]["final"]["name"] == "용접봉"
    # 짝이 없는 행은 셀 강조 대상이 아니다 — 항목 수 불일치로 이미 드러난다.
    assert data["rows"][1]["mismatch"] == []


def test_entry_detail_404_when_draft_absent(client, uploads):
    res = client.get("/api/hermes/entries/424242")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_entry_detail_500_when_raw_json_is_corrupt(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    (uploads / f"{invoice_id}.raw.json").write_text("{ not json", encoding="utf-8")
    res = client.get(f"/api/hermes/entries/{invoice_id}")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SERVER_ERROR"


# --- 원본 사진 ---


def test_photo_returns_raw_bytes(client, uploads, db_conn):
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    (uploads / f"{invoice_id}.jpg").write_bytes(b"\xff\xd8\xffbytes")
    res = client.get(f"/api/hermes/entries/{invoice_id}/photo")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8\xffbytes"
    # envelope 예외 — JSON이 아니다.
    assert res.headers["content-type"].startswith("image/")


def test_photo_404_when_file_absent(client, uploads, db_conn):
    """목록·상세는 has_photo=false로 살아 있고, 사진 직접 호출만 404다(spec §4.2)."""
    invoice_id = _seed_invoice(db_conn)
    _write_draft(uploads, invoice_id)
    assert client.get(f"/api/hermes/entries/{invoice_id}").status_code == 200
    assert client.get(f"/api/hermes/entries/{invoice_id}/photo").status_code == 404


def test_photo_404_when_draft_absent(client, uploads):
    assert client.get("/api/hermes/entries/424242/photo").status_code == 404
