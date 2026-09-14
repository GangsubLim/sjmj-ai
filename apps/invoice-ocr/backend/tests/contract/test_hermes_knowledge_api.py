"""GET /api/hermes/knowledge/versions 계약 — 지식 버전 changelog(직전 발행 대비 절별 변경).

데이터 루트를 tmp_path로 갈아끼운다(test_hermes_api.py와 같은 이유 — data_root()는 매 호출
os.environ을 읽는다).
"""

import json

import pytest

pytestmark = pytest.mark.usefixtures("db_conn")

_KEYS = {"version", "published_at", "corrections_through", "rejected", "changes", "missing_file"}


@pytest.fixture
def kdir(tmp_path, monkeypatch):
    """SJMJ_DATA_DIR을 임시 루트로 바꾸고 agent_knowledge/knowledge까지 만들어 kdir을 준다."""
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    d = tmp_path / "agent_knowledge"
    (d / "knowledge").mkdir(parents=True)
    return d


def _md(rules: list[str]) -> str:
    body = "\n".join(f"- {r}" for r in rules) or "(없음)"
    return f"# sjmj 판독 지식\n\n## 일반화 규칙\n\n{body}\n"


def _publish(kdir, version: int, rules: list[str], *, write_file=True, at=None) -> None:
    record = {
        "version": version,
        "published_at": at or f"2026-09-{version:02d}T03:00:00",
        "corrections_through": version * 10,
    }
    with (kdir / "versions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if write_file:
        (kdir / "knowledge" / f"v{version}.md").write_text(_md(rules), encoding="utf-8")


def _reject(kdir, current: int, reason: str) -> None:
    record = {"version": current, "published_at": "2026-09-20T03:00:00", "rejected": reason}
    with (kdir / "versions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_versions_returns_success_envelope_latest_first(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    _publish(kdir, 2, ["규칙 A", "규칙 B"])
    res = client.get("/api/hermes/knowledge/versions")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert [row["version"] for row in body["data"]] == [2, 1]
    assert all(set(row) == _KEYS for row in body["data"])


def test_versions_diff_is_against_previous_version_file(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    _publish(kdir, 2, ["규칙 A", "규칙 B"])
    latest = client.get("/api/hermes/knowledge/versions").json()["data"][0]
    assert latest["corrections_through"] == 20
    assert latest["rejected"] is None
    assert latest["missing_file"] is False
    assert latest["changes"] == [
        {
            "section": "일반화 규칙",
            "added": [{"group": "", "text": "규칙 B"}],
            "removed": [],
            "moved": [],
            "changed": [],
        }
    ]


def test_versions_identical_content_yields_empty_changes(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    _publish(kdir, 2, ["규칙 A"])
    assert client.get("/api/hermes/knowledge/versions").json()["data"][0]["changes"] == []


def test_versions_first_publish_has_null_changes(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    (row,) = client.get("/api/hermes/knowledge/versions").json()["data"]
    assert row["changes"] is None
    assert row["missing_file"] is False


def test_versions_rejected_record_is_listed_without_diff(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    _reject(kdir, 1, "헤딩 누락")
    rows = client.get("/api/hermes/knowledge/versions").json()["data"]
    assert [(r["version"], r["rejected"]) for r in rows] == [(1, "헤딩 누락"), (1, None)]
    rejected = rows[0]
    assert rejected["published_at"] == "2026-09-20T03:00:00"
    assert rejected["corrections_through"] is None
    assert rejected["changes"] is None
    assert rejected["missing_file"] is False


def test_versions_missing_own_file_is_flagged_not_500(client, kdir):
    _publish(kdir, 1, ["규칙 A"])
    _publish(kdir, 2, ["규칙 B"], write_file=False)
    latest = client.get("/api/hermes/knowledge/versions").json()["data"][0]
    assert latest["changes"] is None
    assert latest["missing_file"] is True


def test_versions_missing_previous_file_is_flagged_not_500(client, kdir):
    _publish(kdir, 1, ["규칙 A"], write_file=False)
    _publish(kdir, 2, ["규칙 B"])
    latest = client.get("/api/hermes/knowledge/versions").json()["data"][0]
    assert latest["changes"] is None
    assert latest["missing_file"] is True


def test_versions_first_publish_missing_own_file_is_flagged(client, kdir):
    """v1도 자기 파일 부재는 missing_file — 직전이 없다고 '초기 발행'으로 접으면 안 된다."""
    _publish(kdir, 1, ["규칙 A"], write_file=False)
    (row,) = client.get("/api/hermes/knowledge/versions").json()["data"]
    assert row["changes"] is None
    assert row["missing_file"] is True


def test_versions_empty_when_versions_file_absent(client, kdir):
    res = client.get("/api/hermes/knowledge/versions")
    assert res.status_code == 200
    assert res.json()["data"] == []


def test_versions_does_not_create_knowledge_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    assert client.get("/api/hermes/knowledge/versions").json()["data"] == []
    assert not (tmp_path / "agent_knowledge").exists()
