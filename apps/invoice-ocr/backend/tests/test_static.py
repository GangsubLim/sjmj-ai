"""정적 dist 서빙 / SPA fallback 분기 테스트."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_no_static_dir_keeps_health(monkeypatch, tmp_path: Path) -> None:
    """dist가 없으면 정적 마운트를 건너뛰고 /health는 그대로 200."""
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(tmp_path / "missing"))
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    # 정적 마운트가 없으므로 임의 경로는 404
    assert client.get("/").status_code == 404


def test_static_dir_serves_spa(monkeypatch, tmp_path: Path) -> None:
    """dist가 있으면 / 와 임의 경로가 index.html로 fallback."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>sjmj-ai</title>")
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(tmp_path))
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200  # API 우선
    root = client.get("/")
    assert root.status_code == 200
    assert "sjmj-ai" in root.text
    # 클라이언트 라우팅 경로도 index.html로
    assert client.get("/list").status_code == 200
