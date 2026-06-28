"""scripts/backup-db.sh 검증 — fake mysqldump로 실 MySQL 없이 산출물/retain 확인."""

import gzip
import os
import stat
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _REPO_ROOT / "scripts" / "backup-db.sh"


def _fake_mysqldump(bin_dir: Path) -> None:
    """stdout에 더미 SQL을 뱉는 가짜 mysqldump를 PATH 앞단에 설치."""
    fake = bin_dir / "mysqldump"
    fake.write_text(
        "#!/usr/bin/env bash\necho '-- dump'\necho 'CREATE TABLE t (id INT);'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _write_env(path: Path) -> None:
    path.write_text(
        "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=sjmj\nDB_USER=root\nDB_PASS=\n"
    )


def test_backup_creates_gzip_and_retains(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_mysqldump(bin_dir)
    env_file = tmp_path / "backend.env"
    _write_env(env_file)
    backup_dir = tmp_path / "backups"

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    # 12회 실행 → keep=10 이면 최종 10개만 남아야 함. 타임스탬프 충돌 방지 위해 인덱스 접미사 주입은
    # 스크립트가 초 단위 → 빠른 반복은 동일 파일명이 될 수 있어, 백업 파일명에 nanosecond/seq를 쓴다(아래 구현).
    for _ in range(12):
        result = subprocess.run(
            [
                "bash",
                str(_SCRIPT),
                "--env",
                str(env_file),
                "--backup-dir",
                str(backup_dir),
                "--keep",
                "10",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    dumps = sorted(backup_dir.glob("sjmj-*.sql.gz"))
    assert len(dumps) == 10, f"expected 10 retained, got {len(dumps)}"
    # 내용이 gzip + 더미 SQL 인지 확인
    assert "CREATE TABLE t" in gzip.decompress(dumps[-1].read_bytes()).decode()


def test_backup_fails_without_db_name(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_mysqldump(bin_dir)
    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_USER=root\nDB_PASS=\n"
    )  # DB_NAME 누락
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--env",
            str(env_file),
            "--backup-dir",
            str(tmp_path / "b"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DB_NAME" in result.stderr
