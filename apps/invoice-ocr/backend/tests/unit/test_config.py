from app import config


def _settings(monkeypatch, env: dict):
    for k in ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    return config.get_settings()


def test_db_env_override(monkeypatch):
    s = _settings(
        monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": "p"}
    )
    assert (s.db_host, s.db_name, s.db_user, s.db_pass) == ("db1", "n", "u", "p")


def test_empty_password_respected(monkeypatch):
    # 빈 비밀번호는 유효한 값 — 미설정과 구분(AppConfig 골든 동치)
    s = _settings(
        monkeypatch, {"DB_HOST": "db1", "DB_NAME": "n", "DB_USER": "u", "DB_PASS": ""}
    )
    assert s.db_pass == ""


def test_defaults_when_unset(monkeypatch):
    s = _settings(monkeypatch, {})
    assert s.db_host == "localhost"
    assert s.db_name == "kslim"
    assert s.db_port == 3306
