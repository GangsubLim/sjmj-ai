# sjmj-ai CD 구성 구현 플랜 — macmini 태그 배포 + release 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** sjmj-ai에 `v*` 태그 push → macmini self-hosted 러너 배포(빌드·launchd 재시작·health·실패 시 롤백) CD와, 버전 단일 진실원 + release 워크플로우(`release.sh` + `my-release` 스킬)를 갖춘다.

**Architecture:** donboksa의 CD 패턴을 sjmj-ai 단일 서비스(`ai.sjmj.backend` :8400가 프론트 dist까지 서빙)·모노레포 경로(`apps/invoice-ocr/{backend,frontend}`)·운영 PII DB(`sjmj`)에 맞게 이식한다. 루트 `VERSION`을 진실원으로 두고 `app/config.py:APP_VERSION`과 `scripts/sync-version.sh`로 단일화한다. CI(PR, ubuntu)는 `services.mysql`로 권위 있게 pytest를 돌리고, deploy(태그, macmini)는 import 스모크만 수행해 운영 머신에 테스트 DB 의존을 만들지 않는다.

**Tech Stack:** GitHub Actions(self-hosted macmini 러너 + ubuntu), bash, uv/pytest(FastAPI+SQLAlchemy+pymysql), npm/vite/vitest, launchd, mysqldump, gh CLI.

## Global Constraints

- 버전 진실원 = 루트 `VERSION`. baseline `0.1.0`(현 `APP_VERSION`·운영 `/health`와 일치). `0.0.0` 채택 금지.
- `VERSION`과 `apps/invoice-ocr/backend/app/config.py:APP_VERSION` 두 값만 동기. 패키지 version 필드(`backend/pyproject.toml`, `frontend/package.json`)는 건드리지 않는다(publish 없이 tag 배포).
- 운영 데이터 덤프(실고객 PII)는 레포 커밋 금지. 백업 산출물은 `~/sjmj-backups/`(레포 밖). `.gitignore`는 이미 `db/*backup*.sql`·`*.env`(예외 `*.env.example`)를 차단.
- 시크릿 하드코딩 금지. DB 접속·대상 DB명은 `~/.sjmj-ai/backend.env`의 `DB_*`에서만 읽는다. backup 대상 DB명 하드코딩 금지.
- 운영 클론 경로 = `/Users/submini/sjmj-ai`. 단일 launchd 라벨 = `ai.sjmj.backend`. health = `http://127.0.0.1:8400/health`. 외부 노출 `tailscale serve --https=8443`은 배포가 건드리지 않는다.
- 리포 owner = `GangsubLim/sjmj-ai`. gh 활성 계정 = `GangsubLim`.
- 기본 브랜치 = `main`, 개발 = `devel`. 구현은 `devel`에서 진행(현재 브랜치).

---

## File Structure

| 파일 | 책임 | 신규/수정 |
| --- | --- | --- |
| `VERSION` | 버전 진실원 (`0.1.0`) | 신규 |
| `CHANGELOG.md` | Keep a Changelog 4카테고리 | 신규 |
| `scripts/sync-version.sh` | `<x.y.z>` 받아 `VERSION` + `config.py:APP_VERSION` 동기 갱신(순수, git 무관) | 신규 |
| `scripts/release.sh` | main 가드 → 버전계산 → 로컬검증 → sync-version 호출 + CHANGELOG → release 브랜치 | 신규 |
| `scripts/backup-db.sh` | env의 `DB_*`로 `sjmj` mysqldump → `~/sjmj-backups/` gz, 최근 10 retain | 신규 |
| `apps/invoice-ocr/backend/tests/test_version_sync.py` | `VERSION` == `APP_VERSION` 불변식 잠금 | 신규 |
| `apps/invoice-ocr/backend/tests/test_health.py` | 리터럴 → `APP_VERSION` import 비교(bump-proof) | 수정 |
| `apps/invoice-ocr/backend/tests/test_backup_db.py` | backup-db.sh를 fake mysqldump로 검증(산출물·retain) | 신규 |
| `deploy/env/backend.env.example` | `DB_*` 추가(`DB_NAME=sjmj` 안내) | 수정 |
| `.github/workflows/ci.yml` | PR(main/devel): lint(ruff)·frontend·backend(+MySQL service) | 신규 |
| `.github/workflows/deploy.yml` | 태그(v*): macmini 배포 + 롤백 | 신규 |
| `.claude/skills/my-release/SKILL.md` | sjmj 릴리스 9단계 스킬 | 신규 |
| `docs/superpowers/runbooks/macmini-runner.md` | 러너 등록 런북 | 신규 |

---

## Task 1: 버전 단일 진실원 (VERSION + sync-version.sh + bump-proof 테스트)

버전 이중성(루트 VERSION 부재 + `APP_VERSION` 하드코딩 + `test_health` 리터럴 기대)을 제거한다. `sync-version.sh`(git 무관 순수 스크립트)를 먼저 만들어 TDD하고, 이를 release.sh(Task 3)가 소비한다.

**Files:**
- Create: `VERSION`
- Create: `CHANGELOG.md`
- Create: `scripts/sync-version.sh`
- Create: `apps/invoice-ocr/backend/tests/test_version_sync.py`
- Modify: `apps/invoice-ocr/backend/tests/test_health.py:15`
- Test: `apps/invoice-ocr/backend/tests/test_version_sync.py`

**Interfaces:**
- Produces: `scripts/sync-version.sh <x.y.z>` — 루트 `VERSION`에 `<x.y.z>\n` 기록, `apps/invoice-ocr/backend/app/config.py`의 `APP_VERSION = "..."` 라인을 `APP_VERSION = "<x.y.z>"`로 치환. 잘못된 버전 형식이면 exit 1. (Task 3 release.sh가 호출)
- Produces: 루트 `VERSION` 파일 = 현재 앱 버전 문자열(끝 개행 1개).

- [ ] **Step 1: 버전 동기 불변식 실패 테스트 작성**

`apps/invoice-ocr/backend/tests/test_version_sync.py`:

```python
"""버전 단일 진실원 불변식 — 루트 VERSION == app.config.APP_VERSION."""
from pathlib import Path

from app.config import APP_VERSION

# 백엔드 패키지 기준 레포 루트: app/config.py → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_version_file_matches_app_version() -> None:
    """루트 VERSION 파일과 APP_VERSION이 일치해야 한다(release.sh 동기 갱신 보장)."""
    version_file = _REPO_ROOT / "VERSION"
    assert version_file.is_file(), f"missing VERSION at {version_file}"
    assert version_file.read_text().strip() == APP_VERSION
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/test_version_sync.py -v`
Expected: FAIL — `missing VERSION at .../VERSION` (루트 VERSION 아직 없음)

- [ ] **Step 3: VERSION + CHANGELOG 생성**

`VERSION` (끝 개행 포함):

```
0.1.0
```

`CHANGELOG.md`:

```markdown
# Changelog

이 프로젝트의 주요 변경 사항을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/),
버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

## [Unreleased]

### Added

- 루트 `VERSION` 진실원 + macmini 태그 배포 CD(`deploy.yml`) + CI(`ci.yml`) + `release.sh`/`my-release` 스킬
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/test_version_sync.py -v`
Expected: PASS

- [ ] **Step 5: sync-version.sh 작성**

`scripts/sync-version.sh`:

```bash
#!/usr/bin/env bash
# 버전 동기 갱신 — 루트 VERSION + apps/invoice-ocr/backend/app/config.py:APP_VERSION.
# git/브랜치와 무관한 순수 파일 치환(release.sh가 호출, 단독 테스트 가능).
#
# 사용: scripts/sync-version.sh <x.y.z>
set -euo pipefail

NEW="${1:-}"
echo "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || {
  echo "ERROR: 버전 형식이 x.y.z 가 아님: '$NEW'" >&2
  exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/apps/invoice-ocr/backend/app/config.py"

printf '%s\n' "$NEW" >"$REPO_ROOT/VERSION"

# APP_VERSION = "x.y.z" 한 줄만 치환(따옴표 안 내용 교체). 다른 줄 불변.
python3 - "$CONFIG" "$NEW" <<'PY'
import re
import sys

path, new = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
updated, n = re.subn(
    r'^APP_VERSION\s*=\s*".*"',
    f'APP_VERSION = "{new}"',
    text,
    count=1,
    flags=re.MULTILINE,
)
if n != 1:
    sys.exit(f"ERROR: APP_VERSION 라인을 {path} 에서 못 찾음")
open(path, "w", encoding="utf-8").write(updated)
PY

echo "synced VERSION + APP_VERSION → $NEW"
```

- [ ] **Step 6: sync-version.sh 동작 검증(임시 버전으로 왕복)**

```bash
chmod +x scripts/sync-version.sh
scripts/sync-version.sh 0.1.0   # 멱등: 이미 0.1.0
grep -n 'APP_VERSION = "0.1.0"' apps/invoice-ocr/backend/app/config.py
cat VERSION
# 형식 거부 검증:
scripts/sync-version.sh 1.2 && echo "BUG: should have failed" || echo "ok: rejected bad format"
```

Expected: config.py에 `APP_VERSION = "0.1.0"` 존재, `VERSION`=`0.1.0`, 잘못된 형식은 `ok: rejected bad format`.

- [ ] **Step 7: test_health를 bump-proof로 수정**

`apps/invoice-ocr/backend/tests/test_health.py` 상단 import에 추가하고 리터럴 비교를 교체:

```python
"""/health 헬스체크 테스트."""
from fastapi.testclient import TestClient

from app.config import APP_VERSION
from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    """GET /health → 200 + status=ok + version."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == APP_VERSION
```

(`test_api_health_returns_ok`는 변경 없음.)

- [ ] **Step 8: 헬스 테스트 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/test_health.py tests/test_version_sync.py -v`
Expected: PASS (4 passed)

- [ ] **Step 9: 커밋**

```bash
git add VERSION CHANGELOG.md scripts/sync-version.sh \
  apps/invoice-ocr/backend/tests/test_version_sync.py \
  apps/invoice-ocr/backend/tests/test_health.py \
  apps/invoice-ocr/backend/app/config.py
git commit -m "feat(release): 버전 단일 진실원 — VERSION 0.1.0 + sync-version.sh + bump-proof health 테스트"
```

---

## Task 2: mysqldump 백업 스크립트 + env 예시

deploy 시작 시(롤백 앵커 확보용)와 운영자 수동 호출 겸용. 대상 DB명·접속을 env에서만 읽어 런타임/백업 DB 발산을 막는다. fake `mysqldump`로 TDD(실 MySQL 불필요).

**Files:**
- Create: `scripts/backup-db.sh`
- Create: `apps/invoice-ocr/backend/tests/test_backup_db.py`
- Modify: `deploy/env/backend.env.example`
- Test: `apps/invoice-ocr/backend/tests/test_backup_db.py`

**Interfaces:**
- Consumes: env 파일(`--env <path>` 또는 `SJMJ_ENV_FILE`, 기본 `~/.sjmj-ai/backend.env`)의 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS`.
- Produces: `scripts/backup-db.sh [--env <path>] [--backup-dir <dir>] [--keep <n>]` — `$DB_NAME` 덤프를 `<backup-dir>/sjmj-<YYYYMMDDTHHMMSS>.sql.gz`로 생성, 최근 `--keep`(기본 10)개 retain. (Task 5 deploy.yml이 호출)

- [ ] **Step 1: 실패 테스트 작성(fake mysqldump로 산출물·retain 검증)**

`apps/invoice-ocr/backend/tests/test_backup_db.py`:

```python
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
    fake.write_text("#!/usr/bin/env bash\necho '-- dump'\necho 'CREATE TABLE t (id INT);'\n")
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
            ["bash", str(_SCRIPT), "--env", str(env_file),
             "--backup-dir", str(backup_dir), "--keep", "10"],
            env=env, capture_output=True, text=True,
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
    env_file.write_text("DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_USER=root\nDB_PASS=\n")  # DB_NAME 누락
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--env", str(env_file), "--backup-dir", str(tmp_path / "b")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "DB_NAME" in result.stderr
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/test_backup_db.py -v`
Expected: FAIL — `scripts/backup-db.sh` 없음(No such file)

- [ ] **Step 3: backup-db.sh 작성**

`scripts/backup-db.sh`:

```bash
#!/usr/bin/env bash
# 운영 MySQL(sjmj) 일관 백업 → ~/sjmj-backups/sjmj-<ts>.sql.gz, 최근 N개 retain.
# 접속/대상 DB명은 env(DB_*)에서만 읽는다 — 하드코딩 금지(런타임/백업 DB 발산 방지).
# deploy.yml 백업 단계 + 운영자 수동 호출 겸용. 읽기 전용 덤프(비파괴).
#
# 사용: backup-db.sh [--env <path>] [--backup-dir <dir>] [--keep <n>]
set -euo pipefail

ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/backend.env}"
BACKUP_DIR="$HOME/sjmj-backups"
KEEP=10

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help) echo "usage: backup-db.sh [--env <path>] [--backup-dir <dir>] [--keep <n>]"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASS="${DB_PASS:-}"
if [[ -z "${DB_NAME:-}" ]]; then
  echo "ERROR: DB_NAME 이 env($ENV_FILE)에 없음 — 백업 대상 DB명을 명시해야 한다." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
# 초 단위 충돌 방지: 타임스탬프 + nanosecond. macOS date 는 %N 미지원이라 python 으로 생성.
TS="$(python3 -c "import datetime; print(datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f'))")"
DEST="$BACKUP_DIR/sjmj-$TS.sql.gz"

# 비밀번호는 MYSQL_PWD 로 전달(프로세스 목록 노출 회피). 빈 비번도 그대로 존중.
MYSQL_PWD="$DB_PASS" mysqldump \
  --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" \
  --single-transaction --quick --no-tablespaces \
  "$DB_NAME" | gzip >"$DEST"

# retain — 최근 KEEP 개만 유지. macmini /bin/bash 3.2 호환(mapfile 미사용).
ALL=()
while IFS= read -r _line; do ALL+=("$_line"); done \
  < <(ls -1t "$BACKUP_DIR"/sjmj-*.sql.gz 2>/dev/null)
if (( ${#ALL[@]} > KEEP )); then
  for old in "${ALL[@]:KEEP}"; do rm -f "$old"; done
fi

echo "backup ok: $DEST (kept<=$KEEP)"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/backend && uv run pytest tests/test_backup_db.py -v`
Expected: PASS (2 passed)

> macmini `mysqldump --single-transaction`은 InnoDB 일관 스냅샷을 비차단으로 뜬다. `MYSQL_PWD` 빈값은 무비번 운영 env(Phase 1D root/무비번)와 동치.

- [ ] **Step 5: backend.env.example에 DB_* 추가**

`deploy/env/backend.env.example` 끝에 추가:

```
# 런타임 + 백업 공통 DB 접속(backup-db.sh와 단일 출처 공유).
# Phase 1D 운영 실측 = root / 무비번 / 127.0.0.1 / DB_NAME=sjmj.
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=sjmj
DB_USER=root
DB_PASS=
```

- [ ] **Step 6: 커밋**

```bash
chmod +x scripts/backup-db.sh
git add scripts/backup-db.sh apps/invoice-ocr/backend/tests/test_backup_db.py deploy/env/backend.env.example
git commit -m "feat(deploy): mysqldump 백업 스크립트(env DB_* 단일출처) + env.example DB_* 추가"
```

---

## Task 3: release.sh (donboksa 이식 + APP_VERSION 동기 + 모노레포 경로)

main 가드·버전계산·중복검사·로컬검증(CI 게이트 미러)·CHANGELOG·release 브랜치 생성. 버전 갱신은 Task 1의 `sync-version.sh`에 위임(DRY). git 가드 경로는 main 브랜치 전제라 단위테스트 대신 `--dry-run`으로 검증한다.

**Files:**
- Create: `scripts/release.sh`
- Test: 수동(`--dry-run` + `-h`) — main 브랜치 가드 때문에 자동 단위테스트 비대상. 버전 동기 로직은 Task 1에서 이미 커버.

**Interfaces:**
- Consumes: `scripts/sync-version.sh <x.y.z>` (Task 1).
- Produces: `scripts/release.sh <patch|minor|major|x.y.z> [--skip-verify] [--dry-run]` — `release/vX.Y.Z` 브랜치 + `release: vX.Y.Z` 커밋(VERSION·config.py·CHANGELOG 변경 포함). (Task 6 my-release 스킬이 호출)

- [ ] **Step 1: release.sh 작성**

`scripts/release.sh`:

```bash
#!/usr/bin/env bash
# sjmj-ai release — 루트 VERSION(진실원) + config.py:APP_VERSION 동기 + CHANGELOG bump
#                   → release/vX.Y.Z 브랜치 생성.
#
# 사용: scripts/release.sh <patch|minor|major|x.y.z> [--skip-verify] [--dry-run]
#
# 동작:
#   1. main 브랜치 + 워킹트리 클린 + origin/main 동기 검증
#   2. VERSION 읽어 다음 버전 계산 + 태그/브랜치(로컬·원격) 중복 선검사
#   3. 로컬 검증 — PR CI 게이트 미러(ruff backend + eslint/format:check frontend)
#   4. sync-version.sh 로 VERSION + config.py:APP_VERSION 갱신 + CHANGELOG 헤더 prepend
#   5. release/vX.Y.Z 브랜치 + 커밋
#
# 이후(my-release 스킬): CHANGELOG 본문 → push → PR(main) → CI → merge
#   → 태그 push(= deploy.yml 트리거) → devel 동기화 → GitHub Release.
#
# 패키지 version 필드(backend pyproject / frontend package.json)는 건드리지 않는다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_DIR="apps/invoice-ocr/backend"
FRONTEND_DIR="apps/invoice-ocr/frontend"

BUMP=""
SKIP_VERIFY=0
DRY_RUN=0
for a in "$@"; do
  case "$a" in
    --skip-verify) SKIP_VERIFY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
    -*) echo "ERROR: 알 수 없는 옵션: $a" >&2; exit 1 ;;
    *) BUMP="$a" ;;
  esac
done
[ -n "$BUMP" ] || { echo "ERROR: bump 인자 필요 (patch|minor|major|x.y.z). -h 로 도움말." >&2; exit 1; }

# 1. 브랜치 / 워킹트리 / origin 동기 검증
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "ERROR: main 브랜치에서 실행해야 함 (현재: $BRANCH)." >&2
  echo "       'git checkout main && git pull origin main' 후 재실행." >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: 워킹 트리에 커밋되지 않은 변경이 있음. 정리 후 재실행." >&2
  exit 1
fi
if ! git fetch --quiet origin main 2>/dev/null; then
  echo "ERROR: origin 에서 main fetch 실패(네트워크?). 확인 후 재실행." >&2
  exit 1
fi
if [ "$(git rev-parse HEAD)" != "$(git rev-parse FETCH_HEAD)" ]; then
  echo "ERROR: 로컬 main 이 origin/main 과 불일치. 'git pull origin main' 후 재실행." >&2
  exit 1
fi

# 2. 버전 계산
CUR="$(cat VERSION 2>/dev/null || echo 0.0.0)"
IFS=. read -r MA MI PA <<<"$CUR"
case "$BUMP" in
  major) NEW="$((MA + 1)).0.0" ;;
  minor) NEW="${MA}.$((MI + 1)).0" ;;
  patch) NEW="${MA}.${MI}.$((PA + 1))" ;;
  v*) NEW="${BUMP#v}" ;;
  *) NEW="$BUMP" ;;
esac
echo "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "ERROR: 잘못된 버전 형식: $NEW" >&2; exit 1; }
TAG="v$NEW"
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: 태그 $TAG 이미 존재. 다른 버전을 지정하라." >&2; exit 1
fi
if git show-ref --verify --quiet "refs/heads/release/$TAG"; then
  echo "ERROR: 브랜치 release/$TAG 이미 존재(이전 시도 잔여). 삭제 후 재실행: git branch -D release/$TAG" >&2; exit 1
fi
if [ -n "$(git ls-remote --heads origin "release/$TAG" 2>/dev/null)" ]; then
  echo "ERROR: 원격 브랜치 origin/release/$TAG 이미 존재. 삭제 후 재실행: git push origin :release/$TAG" >&2; exit 1
fi
echo "버전: $CUR → $NEW  (tag $TAG)"

# 3. 로컬 검증 — PR CI 게이트 미러
if [ "$SKIP_VERIFY" = "0" ]; then
  echo "== 검증: ruff (backend) =="
  (cd "$BACKEND_DIR" && uvx ruff format --check . && uvx ruff check .)
  echo "== 검증: eslint + format:check (frontend) =="
  (cd "$FRONTEND_DIR" && { [ -d node_modules ] || npm ci; } && npm run lint && npm run format:check)
  echo "검증 통과. (pytest + frontend build 는 배포 시 macmini runner 에서 실행됨)"
else
  echo "검증 건너뜀 (--skip-verify)"
fi

# 4. VERSION + APP_VERSION + CHANGELOG 갱신
DATE="$(date +%F)"
HEADER="## [$TAG] — $DATE"
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] sync-version.sh $NEW (VERSION + config.py:APP_VERSION)"
  echo "[dry-run] CHANGELOG prepend ← \"$HEADER\""
  echo "[dry-run] 브랜치/커밋 생략."
  exit 0
fi
scripts/sync-version.sh "$NEW"
awk -v hdr="$HEADER" '
  BEGIN { done = 0 }
  /^## \[/ && !done { print hdr "\n"; done = 1 }
  { print }
  END { if (!done) print "\n" hdr "\n" }
' CHANGELOG.md >CHANGELOG.tmp && mv CHANGELOG.tmp CHANGELOG.md

# 5. release 브랜치 + 커밋
git checkout -b "release/$TAG"
git add VERSION CHANGELOG.md "$BACKEND_DIR/app/config.py"
git commit -m "release: $TAG"

cat <<EOF

release/$TAG 브랜치 생성 + 커밋 완료.
다음 단계 (my-release 스킬 참조):
  1) CHANGELOG.md 의 "$HEADER" 아래 본문(Added/Changed/Fixed/Removed) 작성
     → git add CHANGELOG.md && git commit --amend --no-edit
  2) git push origin release/$TAG
  3) gh pr create --base main --head release/$TAG --title "release: $TAG"
  4) CI 통과 후 merge
  5) git checkout main && git pull && git tag $TAG && git push origin $TAG   # ← 배포 트리거
  6) git checkout devel && git merge main && git push
  7) gh release create $TAG --title "$TAG" --generate-notes
EOF
```

- [ ] **Step 2: 문법 + 도움말 + dry-run 검증**

```bash
chmod +x scripts/release.sh
bash -n scripts/release.sh && echo "syntax ok"
scripts/release.sh -h
```

Expected: `syntax ok` + 도움말 출력. (dry-run은 main 브랜치에서만 의미 있어 Task 6/실릴리스에서 확인 — 현재 `devel`이면 "main 브랜치에서 실행해야 함"으로 fail-fast 하는 게 정상 동작.)

- [ ] **Step 3: main 가드 동작 확인(devel에서 거부되는지)**

```bash
scripts/release.sh patch --dry-run && echo "BUG" || echo "ok: devel에서 거부됨(main 가드)"
```

Expected: `ok: devel에서 거부됨(main 가드)`

- [ ] **Step 4: 커밋**

```bash
git add scripts/release.sh
git commit -m "feat(release): release.sh — main 가드·CI 게이트 미러·sync-version 위임·release 브랜치"
```

---

## Task 4: CI 워크플로우 (`ci.yml`)

PR(→main/devel)에서 lint(ruff)·frontend·backend(+MySQL service)를 권위 있게 검사한다. 루트 prettier 설정이 없으므로 prettier는 frontend의 기존 `format:check`(`src/**`)로 흡수하고, lint job은 ruff만 담당한다.

**Files:**
- Create: `.github/workflows/ci.yml`
- Test: `actionlint`(설치 시) + YAML 파싱. 권위 검증은 실제 PR(Task 6/DoD).

**Interfaces:**
- Consumes: backend `uv.lock`, frontend `package-lock.json`, `tests/fixtures/schema_test.sql`(`_engine` fixture가 적재), `conftest.py`(`DB_*` env).
- Produces: PR 머지 게이트(3 job green).

- [ ] **Step 1: 로컬에서 게이트 명령이 실제로 통과하는지 선확인**

```bash
cd apps/invoice-ocr/backend && uvx ruff format --check . && uvx ruff check .; cd -
cd apps/invoice-ocr/frontend && { [ -d node_modules ] || npm ci; } && npm run lint && npm run format:check && npx vitest run --passWithNoTests && npm run build; cd -
```

Expected: 모두 통과. **실패 시** — ruff 포맷/린트 위반은 `uvx ruff format . && uvx ruff check --fix .`로 교정 후 별도 커밋(이 task의 일부). eslint/prettier 위반도 동일하게 교정.

- [ ] **Step 2: ci.yml 작성**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
    branches: [main, devel]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Ruff lint
        uses: astral-sh/ruff-action@v3
        with:
          version: 0.15.12
          args: check --output-format=github
          src: apps/invoice-ocr/backend
      - name: Ruff format check
        uses: astral-sh/ruff-action@v3
        with:
          version: 0.15.12
          args: format --check
          src: apps/invoice-ocr/backend

  frontend:
    name: frontend
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/invoice-ocr/frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: apps/invoice-ocr/frontend/package-lock.json
      - name: Install dependencies
        run: npm ci
      - name: ESLint
        run: npm run lint
      - name: Prettier check
        run: npm run format:check
      - name: Vitest
        run: npx vitest run --passWithNoTests
      - name: Build
        run: npm run build

  backend:
    name: backend
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/invoice-ocr/backend
    services:
      mysql:
        image: mysql:8
        env:
          MYSQL_ROOT_PASSWORD: ci_root_pw
          MYSQL_DATABASE: sjmj_test
        ports:
          - 3306:3306
        options: >-
          --health-cmd "mysqladmin ping -h 127.0.0.1 -uroot -pci_root_pw"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - name: Install dependencies
        run: uv sync --frozen
      - name: Pytest with coverage
        env:
          DB_HOST: 127.0.0.1
          DB_PORT: "3306"
          DB_NAME: sjmj_test
          DB_USER: root
          DB_PASS: ci_root_pw
        run: uv run pytest -q --cov=app --cov-report=term-missing --cov-fail-under=80
```

> backend job: `conftest.py`의 `_engine` fixture가 빈 `sjmj_test`에 `schema_test.sql`을 적재하므로 별도 schema init step 불필요. conftest 기본 user(`sjmj_test`)를 `root`로 override해 TRUNCATE/DDL 권한 확보. service `MYSQL_ROOT_PASSWORD`는 CI 전용 더미(운영 비밀 아님).

- [ ] **Step 3: 워크플로우 유효성 검증**

```bash
command -v actionlint >/dev/null && actionlint .github/workflows/ci.yml \
  || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
```

Expected: actionlint 통과 또는 `yaml ok`.

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/ci.yml
# (Step 1에서 포맷 교정이 있었다면 해당 파일도 함께)
git commit -m "ci: PR 게이트 — ruff(lint)·frontend(eslint/prettier/vitest/build)·backend(MySQL service+pytest cov≥80)"
```

---

## Task 5: Deploy 워크플로우 (`deploy.yml`)

`v*` 태그 push → macmini 러너에서 백업→빌드→단일 launchd 재시작→health, 실패 시 직전 SHA 자동 롤백. 운영 클론을 직접 `git fetch`/`checkout`(actions/checkout 미사용). pytest 대신 **import 스모크**(운영 머신에 sjmj_test DB 의존 회피; 전체 pytest는 CI가 권위).

**Files:**
- Create: `.github/workflows/deploy.yml`
- Test: `actionlint`/YAML 파싱. 권위 검증은 실제 태그 배포(Task 6/DoD).

**Interfaces:**
- Consumes: `scripts/backup-db.sh`(Task 2), `scripts/install-launchagent.sh`(기존), `scripts/run-backend.sh`(기존), env `~/.sjmj-ai/backend.env`.
- Produces: macmini `ai.sjmj.backend` 재시작 + health 통과 배포(또는 롤백).

- [ ] **Step 1: deploy.yml 작성**

`.github/workflows/deploy.yml`:

```yaml
name: Deploy sjmj-ai to Mac mini

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:

jobs:
  deploy:
    runs-on: [self-hosted, macmini]
    defaults:
      run:
        working-directory: /Users/submini/sjmj-ai

    env:
      SJMJ_ENV_FILE: /Users/submini/.sjmj-ai/backend.env
      HEALTH_URL: http://127.0.0.1:8400/health
      BACKEND_DIR: apps/invoice-ocr/backend
      FRONTEND_DIR: apps/invoice-ocr/frontend

    steps:
      - name: Record previous commit
        id: previous
        run: echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"

      - name: Fetch target ref
        run: git fetch --tags --force origin

      - name: Checkout target
        run: |
          set -euo pipefail
          if [[ "${GITHUB_REF_TYPE}" == "tag" ]]; then
            git checkout --force "${GITHUB_REF_NAME}"
          else
            git checkout --force "${GITHUB_SHA}"
          fi

      - name: Backup operational DB
        run: |
          set -euo pipefail
          chmod +x scripts/backup-db.sh
          SJMJ_ENV_FILE="$SJMJ_ENV_FILE" scripts/backup-db.sh

      - name: Backend deps + import smoke
        run: |
          set -euo pipefail
          cd "$BACKEND_DIR"
          uv sync --frozen
          uv run python -c "from app.main import app; assert app"

      - name: Build frontend
        run: |
          set -euo pipefail
          cd "$FRONTEND_DIR"
          npm ci
          npm run build

      - name: Restart LaunchAgent
        run: |
          set -euo pipefail
          chmod +x scripts/run-backend.sh scripts/install-launchagent.sh
          SJMJ_ENV_FILE="$SJMJ_ENV_FILE" scripts/install-launchagent.sh

      - name: Health check
        run: |
          set -euo pipefail
          ok=0
          for i in $(seq 1 30); do
            if curl -fsS "$HEALTH_URL"; then echo "health ok"; ok=1; break; fi
            sleep 2
          done
          [[ "$ok" == "1" ]] || { echo "health check failed: $HEALTH_URL" >&2; exit 1; }

      - name: Rollback on failure
        if: failure()
        run: |
          set -euo pipefail
          PREV="${{ steps.previous.outputs.sha }}"
          [[ -n "$PREV" ]] || { echo "missing previous sha; cannot rollback" >&2; exit 1; }
          git checkout --force "$PREV"
          cd "$BACKEND_DIR" && uv sync --frozen && uv run python -c "from app.main import app; assert app"
          cd "$GITHUB_WORKSPACE_OVERRIDE_FRONTEND" 2>/dev/null || cd /Users/submini/sjmj-ai/"$FRONTEND_DIR"
          npm ci && npm run build
          cd /Users/submini/sjmj-ai
          SJMJ_ENV_FILE="$SJMJ_ENV_FILE" scripts/install-launchagent.sh
          ok=0
          for i in $(seq 1 30); do
            if curl -fsS "$HEALTH_URL"; then echo "rollback health ok"; ok=1; break; fi
            sleep 2
          done
          [[ "$ok" == "1" ]] || { echo "rollback health check failed" >&2; exit 1; }
```

> rollback의 frontend cd는 절대경로(`/Users/submini/sjmj-ai/apps/invoice-ocr/frontend`)로 단순화한다 — 위 `$GITHUB_WORKSPACE_OVERRIDE_FRONTEND` 분기는 제거하고 아래 Step 2에서 정리한다.

- [ ] **Step 2: rollback frontend 경로 단순화(정리 편집)**

`deploy.yml`의 rollback step에서 frontend cd 두 줄을 다음 한 줄로 교체:

```yaml
          cd /Users/submini/sjmj-ai/"$FRONTEND_DIR"
```

즉 `cd "$GITHUB_WORKSPACE_OVERRIDE_FRONTEND" 2>/dev/null || cd ...` 줄을 삭제하고 절대경로 cd만 남긴다.

- [ ] **Step 3: 워크플로우 유효성 검증**

```bash
command -v actionlint >/dev/null && actionlint .github/workflows/deploy.yml \
  || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml')); print('yaml ok')"
```

Expected: actionlint 통과 또는 `yaml ok`. (actionlint의 self-hosted 라벨 경고는 무시 가능.)

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci(deploy): v* 태그 → macmini 단일서비스 배포(백업→import스모크→build→launchd→health→롤백)"
```

---

## Task 6: `my-release` 스킬

donboksa 9단계 이식 + sjmj 특성(단일 서비스 :8400, deploy 백업 단계, tailscale :8443, 모노레포 경로, `release.sh`가 APP_VERSION 동기). `deleteBranchOnMerge` 설정은 작성 중 `gh` API로 실제 확인해 반영.

**Files:**
- Create: `.claude/skills/my-release/SKILL.md`
- Test: 문서 일관성 셀프체크(경로·포트·URL·리포명).

**Interfaces:**
- Consumes: `scripts/release.sh`(Task 3), `deploy.yml`(Task 5), `backup-db.sh`(Task 2).

- [ ] **Step 1: 리포 머지 설정 확인**

```bash
gh repo view GangsubLim/sjmj-ai --json deleteBranchOnMerge,defaultBranchRef 2>/dev/null \
  || echo "gh 미인증/리포 미존재 — SKILL.md엔 'merge 후 release 브랜치 수동 정리' 전제로 작성"
```

확인 결과(`deleteBranchOnMerge` true/false)를 Step 2 본문 Step 5 주석에 반영한다.

- [ ] **Step 2: SKILL.md 작성**

`.claude/skills/my-release/SKILL.md` — frontmatter + 9단계. 핵심 골격(donboksa 이식, sjmj 치환):

````markdown
---
name: my-release
description: |
  sjmj-ai 전용 릴리스 워크플로우 — 루트 VERSION(진실원, config.py:APP_VERSION과 동기) bump,
  CHANGELOG 작성, release/vX.Y.Z 브랜치 PR 생성, CI watch, merge, 태그 push(= macmini 자동 배포
  트리거), GitHub Release 생성, devel 재동기화까지 9단계를 안내한다. "릴리스", "release",
  "버전 올리자", "vX.Y.Z 내자", "patch/minor/major bump", "배포 준비", "changelog 쓰자",
  "태깅", "배포하자" 등 버전 발행·배포 맥락에서 사용. 단순 "빌드"·"테스트"만이면 트리거 금지.
---

# sjmj-ai Release Workflow

루트 `VERSION`이 버전 진실원이다. `scripts/release.sh`가 bump + `config.py:APP_VERSION` 동기 +
CHANGELOG 헤더 + release 브랜치 생성을 수행한다. 배포는 **`vX.Y.Z` 태그 push**가
`.github/workflows/deploy.yml`을 트리거해 macmini self-hosted runner에서 진행된다.

> **sjmj-ai 특성:**
> - **단일 서비스** `ai.sjmj.backend`(:8400)가 프론트 dist까지 서빙. donboksa의 frontend :8300 없음.
> - deploy 시작 시 **mysqldump 백업**(`scripts/backup-db.sh`, 대상 `sjmj`) 단계가 롤백 앵커 확보.
> - 외부 노출은 `tailscale serve --https=8443`(영속, 배포 무관).
> - 패키지 version 필드 동기 안 함 — 진실원은 루트 `VERSION` + `config.py:APP_VERSION` 둘뿐.
> - 모노레포 경로 `apps/invoice-ocr/{backend,frontend}`.

## Step 1: 사전 확인
git fetch --all --prune --tags / gh pr list --base main / git log main..devel --oneline

## Step 2: 버전 결정 (major/minor/patch 표) — 현 baseline 0.1.0, 첫 CD 릴리스는 patch(0.1.1).

## Step 3: scripts/release.sh <patch|minor|major|x.y.z>
  (main 가드·중복검사·로컬검증[ruff backend + eslint/format:check frontend]·VERSION+APP_VERSION 동기·CHANGELOG·release 브랜치)

## Step 4: CHANGELOG 본문(사용자 관점, Keep a Changelog 4카테고리, 한국어 명령형) → git commit --amend --no-edit

## Step 5: push + PR(main) + CI watch(gh pr checks --watch) + merge
  (deleteBranchOnMerge=<Step1 확인값>에 따라 release 브랜치 수동 정리 여부 명시)

## Step 6: 태그 push → 배포 트리거
  git checkout main && git pull && git tag vX.Y.Z && git push origin vX.Y.Z  # ← deploy.yml 실행

## Step 7: devel 동기화 (git checkout devel && git merge main && git push) — reset 전 git log main..devel 확인

## Step 8: GitHub Release (gh release create vX.Y.Z --generate-notes)

## Step 9: 배포 확인 — gh run watch. run success면 health 통과한 것(deploy.yml이 :8400/health 30회 검증·실패 시 자동 롤백). 직접 보려면 macmini에서 curl -fsS http://127.0.0.1:8400/health.

## gh auth 주의: gh auth switch --user GangsubLim
## 긴급 패치(hotfix) / 트러블슈팅 표(태그 중복·release.sh 검증 실패·PR conflict·deploy 실패 시 macmini 경로/env/runner 점검)
````

> 작성 시 donboksa SKILL.md를 본문 레퍼런스로 삼되 위 치환을 빠짐없이 적용한다(URL의 `donboksa`→`sjmj-ai`, 포트 8200/8300→8400 단일, frontend health 제거, 백업 단계 언급 추가).

- [ ] **Step 3: 일관성 셀프체크**

```bash
grep -nE "8200|8300|donboksa|portfolio" .claude/skills/my-release/SKILL.md && echo "FIX: 잔여 donboksa 토큰" || echo "ok: sjmj 치환 완료"
grep -nE "8400|sjmj-ai|backup-db|deploy.yml|APP_VERSION" .claude/skills/my-release/SKILL.md >/dev/null && echo "ok: sjmj 키워드 존재"
```

Expected: `ok: sjmj 치환 완료` + `ok: sjmj 키워드 존재`.

- [ ] **Step 4: 커밋**

```bash
git add .claude/skills/my-release/SKILL.md
git commit -m "feat(skill): my-release — sjmj 9단계 릴리스 워크플로우(단일서비스·백업단계·APP_VERSION 동기)"
```

---

## Task 7: macmini self-hosted 러너 등록 (운영 런북)

sjmj-ai repo용 러너가 0개라 deploy가 큐에서 멈춘다. augron/donboksa와 동일 방식으로 macmini에 등록한다. SSH로 macmini에서 실행하는 **운영 작업**(코드 TDD 비대상)이라 런북으로 문서화하고, 실행은 사용자 승인/실행 하에 진행한다.

**Files:**
- Create: `docs/superpowers/runbooks/macmini-runner.md`

- [ ] **Step 1: 런북 작성**

`docs/superpowers/runbooks/macmini-runner.md`:

```markdown
# macmini self-hosted 러너 등록 — sjmj-ai

deploy.yml(`runs-on: [self-hosted, macmini]`)이 동작하려면 GangsubLim/sjmj-ai repo에
러너가 online 이어야 한다. (운영 클론 /Users/submini/sjmj-ai 는 Phase 1D에서 존재.)

## 1. 등록 토큰 발급 (개발 머신, gh 인증 GangsubLim)
gh api -X POST repos/GangsubLim/sjmj-ai/actions/runners/registration-token --jq .token

## 2. macmini에서 러너 디렉터리 구성 (SSH)
#   augron/donboksa 러너와 별개 디렉터리. actions-runner 최신 릴리스 사용.
ssh submini@macmini
mkdir -p ~/actions-runner-sjmj-ai && cd ~/actions-runner-sjmj-ai
# (augron/donboksa 러너 디렉터리의 동일 버전 tar 재사용 가능)
./config.sh --url https://github.com/GangsubLim/sjmj-ai \
  --token <위 토큰> --labels self-hosted,macmini --name macmini-sjmj-ai --unattended

## 3. 서비스 등록 + 시작
./svc.sh install
./svc.sh start

## 4. online 확인 (개발 머신)
gh api repos/GangsubLim/sjmj-ai/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
# status == "online", labels에 self-hosted,macmini 포함 확인.
```

- [ ] **Step 2: 런북 검증(토큰 미노출 확인)**

```bash
grep -nE "ghp_|ghs_|github_pat_|password|PASS=" docs/superpowers/runbooks/macmini-runner.md && echo "FIX: 시크릿 노출" || echo "ok: 시크릿 없음"
```

Expected: `ok: 시크릿 없음` (토큰은 발급 명령만, 값은 하드코딩 안 함).

- [ ] **Step 3: 커밋**

```bash
git add docs/superpowers/runbooks/macmini-runner.md
git commit -m "docs(runbook): macmini sjmj-ai self-hosted 러너 등록 절차"
```

- [ ] **Step 4: (사용자 승인 후) 실제 등록 실행 + online 확인**

Step 1의 런북 명령을 SSH로 실행 → `gh api .../actions/runners`에 `status: online` 확인.
Expected: 러너 1개 online(labels: self-hosted, macmini).

---

## 통합 검증 (DoD)

전체 task 완료 후, my-release 스킬로 실제 첫 릴리스(`patch` → `v0.1.1`, "CD 도입")를 수행해 end-to-end 검증:

- [ ] CI: release PR(→main)에서 lint/frontend/backend 3 job green (backend는 `services.mysql`로 `sjmj_test` 접속, 전체 테스트 통과, cov≥80).
- [ ] `test_health`·`test_version_sync`가 `APP_VERSION` 기준 통과, bump 후 `VERSION`·`config.py:APP_VERSION`·`/health` 버전 일치.
- [ ] 러너 online → `v0.1.1` 태그 push → `deploy.yml`이 백업→import스모크→build→launchd 재시작→health 통과로 `success`.
- [ ] `~/sjmj-backups/`에 `sjmj-<ts>.sql.gz` 생성·retain≤10 동작.
- [ ] tailscale `https://...:8443`에서 앱 정상(7페이지 + PDF), 운영 데이터 보존.

---

## Self-Review 메모 (스펙 대비)

- 스펙 §1 버전 → Task 1. §2 CI → Task 4(prettier는 frontend format:check로 흡수: 루트 prettier 설정 부재 반영). §3 deploy → Task 5(pytest 스모크 → import 스모크로 교정: 운영 머신 sjmj_test 의존 회피, 근거 명시). §4 backup-db + env → Task 2. §5 release.sh(+APP_VERSION 동기) → Task 1(sync-version.sh) + Task 3. §6 my-release → Task 6(`.agents/skills` 미러는 donboksa 선례에 없어 YAGNI로 제외, `.claude/skills`만). §7 러너 → Task 7.
- 타입/인터페이스 일관성: `sync-version.sh <x.y.z>`(Task 1 produce → Task 3 consume), `backup-db.sh [--env][--backup-dir][--keep]`(Task 2 produce → Task 5 consume) 시그니처 일치 확인.
