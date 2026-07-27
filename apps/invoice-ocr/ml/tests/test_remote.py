"""tools.remote — 원격 접속 글루의 순수 부분(스크립트 조립·env 기본값)과 실패 처리 단위테스트."""

import shlex
import subprocess

import pytest

from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    RemoteError,
    env_or,
    mysql_script,
    run_ssh,
    source_env,
)


def test_env_or_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_SSH_HOST.name, raising=False)
    assert env_or(ENV_SSH_HOST) == "macmini"


def test_env_or_prefers_environment(monkeypatch):
    monkeypatch.setenv(ENV_SSH_HOST.name, "other-host")
    assert env_or(ENV_SSH_HOST) == "other-host"


def test_env_or_treats_empty_value_as_unset(monkeypatch):
    # 배포 env 파일에서 값이 빈 문자열로 설정된 경우도 미설정과 동일하게 기본값을 쓴다
    # (ocr_poc/config.py의 `if not raw` 관례와 동일).
    monkeypatch.setenv(ENV_SSH_HOST.name, "")
    assert env_or(ENV_SSH_HOST) == "macmini"


def test_source_env_prefix_exports_all_vars_and_fails_fast():
    assert source_env("~/.sjmj-ai/backend.env") == (
        'set -eu; set -a; source "~/.sjmj-ai/backend.env"; set +a; '
    )


def test_mysql_script_uses_env_credentials_and_batch_flags():
    s = mysql_script(ENV_BACKEND_ENV.default, "SELECT 1")
    assert 'set -eu; set -a; source "$HOME/.sjmj-ai/backend.env"; set +a;' in s
    assert 'export MYSQL_PWD="$DB_PASS"' in s
    assert "--batch" in s and "--raw" not in s
    assert '"$DB_NAME"' in s
    assert "SELECT 1" in s


def test_mysql_script_raw_mode_adds_raw_flag():
    assert "--batch --raw" in mysql_script("~/e.env", "SELECT 1", raw=True)


def test_mysql_script_shell_quotes_sql_containing_backticks_and_double_quotes():
    # H1: `-e "{sql}"`는 백틱(원격 명령치환 실행)·이중따옴표(SQL 조용한 변형)에 무방비였다.
    # shlex.quote로 감싸면 셸이 SQL 전체를 리터럴 인자 1개로만 본다.
    sql = 'SELECT * FROM t WHERE x = "a"; -- `whoami`'
    s = mysql_script("~/e.env", sql)
    assert f"-e {shlex.quote(sql)}" in s
    assert f'-e "{sql}"' not in s  # 옛 취약 형태가 재등장하지 않았는지 고정


def test_run_ssh_raises_remote_error_with_host_on_nonzero_exit(monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = b""
        stderr = b"boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted())
    with pytest.raises(RemoteError, match="somehost"):
        run_ssh("somehost", "false")


def test_run_ssh_raises_remote_error_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RemoteError, match="somehost"):
        run_ssh("somehost", "sleep 999")
