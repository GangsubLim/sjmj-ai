import pytest

from app import config


def _settings(monkeypatch, env: dict):
    for k in ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    return config.get_settings()


def test_db_env_override(monkeypatch):
    s = _settings(monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": "p"})
    assert (s.db_host, s.db_name, s.db_user, s.db_pass) == ("db1", "n", "u", "p")


def test_empty_password_respected(monkeypatch):
    # 빈 비밀번호는 유효한 값 — 미설정과 구분
    s = _settings(monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": ""})
    assert s.db_pass == ""


def test_defaults_when_unset(monkeypatch):
    s = _settings(monkeypatch, {})
    assert s.db_host == "localhost"
    assert s.db_name == "kslim"
    assert s.db_port == 3306


def test_data_root_returns_configured_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    assert config.data_root() == tmp_path


def test_data_root_rejects_unset_env(monkeypatch):
    monkeypatch.delenv("SJMJ_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="미설정"):
        config.data_root()


def test_data_root_rejects_nonexistent_dir(tmp_path, monkeypatch):
    """오타·상대경로가 조용히 새 디렉터리로 생성되지 않도록 실재를 검증한다.

    소비처 ocr_service._upload_root()가 mkdir(parents=True)를 하므로, 이 가드가 없으면
    업로드는 200인데 워커 데이터 루트와 어긋나 crop/warped 조회만 전부 404가 된다.
    """
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path / "typo-not-created"))
    with pytest.raises(RuntimeError, match="경로 없음"):
        config.data_root()


def test_crop_dir_layout_matches_worker(tmp_path, monkeypatch):
    """워커(ml/worker/main.py → handwriting/infer_job.py)가 쓰는 레이아웃과 동일해야 한다."""
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    assert config.crop_dir(42) == tmp_path / "ocr_crops" / "job-42"
