"""/health 헬스체크 테스트."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    """GET /health → 200 + status=ok + version."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_api_health_returns_ok() -> None:
    """GET /api/health → 200 (프론트 프록시 경로 일관성)."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
