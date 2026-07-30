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


def _dist_with_asset(tmp_path: Path) -> Path:
    """index.html + 해시 파일명 에셋 1개 + favicon.ico 1개를 가진 dist 레이아웃을 만든다."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<!doctype html><title>sjmj-ai</title>")
    (tmp_path / "favicon.ico").write_text("fake-ico-bytes")
    return tmp_path


def test_spa_index_is_never_stored(monkeypatch, tmp_path: Path) -> None:
    """SPA fallback의 index.html은 Cache-Control: no-store로 응답한다.

    고정명 index.html이 캐시되면 새로고침해도 옛 문서가 재사용되어 옛 해시 청크를
    다시 요청한다(자동 리로드가 무한 루프에 빠지는 전제 조건).
    """
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(_dist_with_asset(tmp_path)))
    client = TestClient(create_app())
    for path in ("/", "/list"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-store", path


def test_direct_index_request_has_no_cache_header(monkeypatch, tmp_path: Path) -> None:
    """/index.html 직접 요청은 실파일 분기를 타므로 no-store가 붙지 않는다(현재 정책 고정).

    SPA fallback(`/`, `/list` 등)의 index.html만 no-store 대상이다(spec 2.2 확정 범위).
    실파일 분기(`candidate.is_file()`)가 `/index.html`을 먼저 잡으므로 캐시 헤더가 전혀
    붙지 않는다 — 버그가 아니라 SPA 진입이 `/`로만 이뤄진다는 전제 하의 수용된 구멍이다.
    """
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(_dist_with_asset(tmp_path)))
    client = TestClient(create_app())
    resp = client.get("/index.html")
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers


def test_real_file_branch_serves_favicon_without_cache_header(monkeypatch, tmp_path: Path) -> None:
    """실파일 분기(favicon.ico 등 정적 자산)는 캐시 헤더 없이 그대로 서빙한다(현재 정책 고정)."""
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(_dist_with_asset(tmp_path)))
    client = TestClient(create_app())
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers


def test_hashed_assets_are_immutable(monkeypatch, tmp_path: Path) -> None:
    """콘텐츠 해시 에셋은 1년 immutable 캐시를 허용한다(200·304 공통)."""
    monkeypatch.setenv("SJMJ_STATIC_DIR", str(_dist_with_asset(tmp_path)))
    client = TestClient(create_app())
    resp = client.get("/assets/index-abc123.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    revalidated = client.get(
        "/assets/index-abc123.js", headers={"If-None-Match": resp.headers["etag"]}
    )
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "public, max-age=31536000, immutable"
