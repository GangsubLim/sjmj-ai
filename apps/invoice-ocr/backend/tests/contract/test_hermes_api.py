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
def client(db_conn):
    """conftest의 `client`를 이 파일 한정으로 덮어쓴다.

    data_root() 미설정 시 RuntimeError가 앱의 전역 `Exception` 핸들러(500)까지 가는데,
    Starlette의 ServerErrorMiddleware는 그 핸들러 응답을 보낸 뒤에도 예외를 항상
    재-raise한다(starlette/middleware/errors.py 주석: "We always continue to raise the
    exception"). 기본 TestClient(raise_server_exceptions=True)는 이걸 그대로 다시 던져
    테스트가 500 응답을 못 보고 예외로 죽는다 — AppError로 명시적으로 던지는 다른 500
    케이스는 ExceptionMiddleware가 처리해 이 경로를 안 타므로 영향이 없다.
    """
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


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


def test_summary_500_when_data_dir_unset(client, monkeypatch):
    monkeypatch.delenv("SJMJ_DATA_DIR", raising=False)
    res = client.get("/api/hermes/summary")
    assert res.status_code == 500
    assert res.json()["success"] is False
