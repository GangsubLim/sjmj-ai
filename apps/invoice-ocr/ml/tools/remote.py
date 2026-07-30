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
# 배포 서버의 ml 디렉터리 — 원격 python이 handwriting 패키지를 import할 cwd이자
# 재평가 산출물(results/bank_update/)의 부모. 원격 홈은 로컬 홈과 다르므로 $HOME 확장은
# 원격 셸에 맡긴다(source_env와 동일 관례).
ENV_ML_ROOT = EnvVar("SJMJ_REMOTE_ML_ROOT", "$HOME/sjmj-ai/apps/invoice-ocr/ml")


class RemoteError(RuntimeError):
    """원격 ssh 실행 실패(비0 종료·타임아웃)."""


def env_or(var: EnvVar) -> str:
    """환경변수 값을 고른다. 미설정이거나 빈 문자열이면 기본값을 쓴다."""
    raw = os.environ.get(var.name)
    if not raw:
        return var.default
    return raw


def remote_path(path: str) -> str:
    """이중따옴표 안에서 쓸 원격 경로로 다듬는다 — `~/` prefix를 `$HOME/`으로 치환한다.

    이중따옴표는 `$VAR` 확장은 유지하지만 `~`는 확장하지 않는다. 인용 전에 치환하지 않으면
    `cd "~/sjmj-ai/..."`가 리터럴 `~` 디렉터리를 찾아 원격에서 즉시 실패한다. 치환을
    source_env 안에만 두면 다른 원격 경로 소비자(SJMJ_REMOTE_ML_ROOT 등)가 같은 함정을
    다시 밟으므로 공유 가능한 형태로 뗀다.

    로컬 `os.path.expanduser`는 쓰지 않는다(로컬 홈 ≠ 원격 홈이라 잘못된 절대경로를 굳힐
    수 있다) — 확장은 원격 셸이 `$HOME`으로 한다. `~user/` 형태는 치환하지 않는다:
    `$HOME`으로 바꾸면 다른 사용자의 홈을 조용히 우리 홈으로 바꿔치기하게 된다.
    """
    return "$HOME/" + path[2:] if path.startswith("~/") else path


def source_env(env_file: str) -> str:
    """원격 env 파일의 값들을 export하는 셸 접두 스크립트를 만든다.

    `set -eu`로 env 파일 부재 시 즉시 실패시킨다(값이 조용히 비어 이후 명령이 알 수
    없는 이유로 실패하는 대신 원인을 바로 드러낸다). 경로는 `remote_path`로 다듬은 뒤
    이중따옴표로 감싸 공백에 안전하되 `$HOME` 같은 변수 확장은 유지한다(작은따옴표였다면
    확장이 막혔을 것).
    """
    return f'set -eu; set -a; source "{remote_path(env_file)}"; set +a; '


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
