"""배포 서버(macmini) 원격 접속 글루 — ssh 실행과 mysql 배치 스크립트 조립.

curation_report와 warp_gate_report가 공유한다. 원격 접속값은 env로만 주입하며
기본값은 현행 배포 관례(deploy/env/ 참조)를 따른다. 실제 ssh 호출은 단위테스트 비대상이고,
스크립트 조립(순수 문자열)과 실패 처리(RemoteError 변환)만 테스트한다.
"""

import os
import shlex
import subprocess
from typing import NamedTuple


class EnvVar(NamedTuple):
    """환경변수 이름과 기본값 쌍."""

    name: str
    default: str


ENV_SSH_HOST = EnvVar("SJMJ_SSH_HOST", "macmini")
ENV_BACKEND_ENV = EnvVar("SJMJ_REMOTE_BACKEND_ENV", "$HOME/.sjmj-ai/backend.env")
ENV_WORKER_ENV = EnvVar("SJMJ_REMOTE_WORKER_ENV", "$HOME/.sjmj-ai/ml-worker.env")


class RemoteError(RuntimeError):
    """원격 ssh 실행 실패(비0 종료·타임아웃)."""


def env_or(var: EnvVar) -> str:
    """환경변수 값을 고른다. 미설정이거나 빈 문자열이면 기본값을 쓴다."""
    raw = os.environ.get(var.name)
    if not raw:
        return var.default
    return raw


def source_env(env_file: str) -> str:
    """원격 env 파일의 값들을 export하는 셸 접두 스크립트를 만든다.

    `set -eu`로 env 파일 부재 시 즉시 실패시킨다(값이 조용히 비어 이후 명령이 알 수
    없는 이유로 실패하는 대신 원인을 바로 드러낸다). 경로는 이중따옴표로 감싸 공백에
    안전하되 `$HOME` 같은 변수 확장은 유지한다(작은따옴표였다면 확장이 막혔을 것).
    """
    return f'set -eu; set -a; source "{env_file}"; set +a; '


def run_ssh(host: str, script: str, *, timeout: float = 600.0) -> bytes:
    """원격 셸 스크립트를 실행하고 stdout(bytes)을 반환한다.

    비0 종료 또는 timeout 초과 시 host를 포함한 RemoteError를 던진다. 기본 timeout
    (600초)은 tar 전송 등 장시간 작업을 고려한 값이다.
    """
    try:
        proc = subprocess.run(
            ["ssh", host, script], capture_output=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise RemoteError(f"ssh 타임아웃({host}, {timeout}s 초과)") from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")[:500]
        raise RemoteError(f"ssh 실패({host}, exit {proc.returncode}): {stderr}")
    return proc.stdout


def mysql_script(backend_env: str, sql: str, *, raw: bool = False) -> str:
    """원격 운영 MySQL에 배치 질의를 던지는 셸 스크립트를 만든다."""
    flags = "--batch --raw" if raw else "--batch"
    return (
        f"{source_env(backend_env)}"
        # 비밀번호는 MYSQL_PWD로 전달(프로세스 목록 노출 회피, scripts/migrate-db.sh 참조).
        'export MYSQL_PWD="$DB_PASS"; '
        'MYSQL_BIN="$(command -v mysql || echo /opt/homebrew/opt/mysql/bin/mysql)"; '
        f'"$MYSQL_BIN" -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" {flags} '
        f"-e {shlex.quote(sql)}"
    )
