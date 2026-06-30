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


def test_spa_fallback_path_traversal_blocked(monkeypatch, tmp_path: Path) -> None:
    """경로 순회 시도 시 static_dir 외부 파일을 반환하지 않고 index.html로 fallback.

    조사 결과: ASGI/httpx 스택은 URL의 %2e%2e, %2f 인코딩을 디코딩·정규화하여
    full_path에 '../secret.txt' 형태로 전달한다 (/../ 절대 경로는 라우터 이전에
    제거됨). 즉 static_dir / '../secret.txt' == static_dir.parent / 'secret.txt'
    로 static_dir 외부를 가리킬 수 있다.

    테스트 레이아웃:
      tmp_path/                  ← 부모 디렉터리
        secret.txt               ← static_dir 외부의 민감 파일
        dist/                    ← static_dir (SJMJ_STATIC_DIR)
          index.html

    /%2e%2e/secret.txt 요청 → full_path='../secret.txt' → candidate가 static_dir
    외부로 해소 → is_relative_to() 검사 실패 → index.html fallback.
    """
    # static_dir 외부에 민감 파일 배치
    secret_content = "TOP SECRET DATA"
    (tmp_path / "secret.txt").write_text(secret_content)

    # static_dir = tmp_path/dist
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    index_content = "<!doctype html><title>sjmj-ai</title>"
    (static_dir / "index.html").write_text(index_content)

    monkeypatch.setenv("SJMJ_STATIC_DIR", str(static_dir))
    client = TestClient(create_app())

    # %2e%2e는 Starlette가 '..'으로 디코딩해서 full_path에 전달한다
    # → static_dir / '../secret.txt' == tmp_path/secret.txt (static_dir 외부)
    resp = client.get("/%2e%2e/secret.txt")
    assert resp.status_code == 200, f"Expected 200 (index fallback), got {resp.status_code}"
    # secret.txt 내용이 아닌 index.html 내용이 반환돼야 한다
    assert secret_content not in resp.text, "Path traversal guard FAILED: secret file was served"
    assert "sjmj-ai" in resp.text, "Expected index.html fallback content"
