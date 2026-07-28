#!/usr/bin/env bash
# 운영 MySQL(sjmj)에 미적용 스키마 마이그레이션을 순번대로 적용한다.
# schema_migrations 원장으로 적용 이력을 추적 — 이미 적용된 파일은 건너뛴다(멱등).
# 접속/대상 DB명은 env(DB_*)에서만 읽는다 — 하드코딩 금지(런타임/백업 DB 발산 방지).
# deploy.yml(백업 직후) + 운영자 수동 호출 겸용. 반드시 backup-db.sh 뒤에 돌린다.
#
# 대상: db/migration_[0-9][0-9][0-9]_*.sql (순번 빌드-오더 마이그레이션).
#   legacy migration_add_*.sql · 1회성 migration_poc_to_production.sql 은 대상 아님(이미 적용).
# 전제: 각 마이그레이션은 재실행 안전(idempotent)해야 한다 — CREATE TABLE IF NOT EXISTS,
#   컬럼 추가는 information_schema 가드(예: migration_008) 등. DDL 은 auto-commit 이라 파일 단위
#   트랜잭션으로 감싸지 못하므로 멱등성이 원장의 안전망을 보완한다.
#
# 사용: migrate-db.sh [--env <path>] [--baseline]
#   --baseline: 파일을 적용하지 않고 현재 대상 전부를 "적용됨"으로 원장에만 기록
#               (기존 운영 DB에 러너를 처음 도입할 때 1회 사용).
set -euo pipefail

ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/backend.env}"
BASELINE=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="$REPO_ROOT/db"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --baseline) BASELINE=1; shift ;;
    -h|--help) echo "usage: migrate-db.sh [--env <path>] [--baseline]"; exit 0 ;;
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
  echo "ERROR: DB_NAME 이 env($ENV_FILE)에 없음 — 대상 DB명을 명시해야 한다." >&2
  exit 1
fi

# 비밀번호는 MYSQL_PWD 로 전달(프로세스 목록 노출 회피). 빈 비번도 그대로 존중.
export MYSQL_PWD="$DB_PASS"
mysql_do() { mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" "$DB_NAME" "$@"; }

# 원장 보장(재실행 안전).
mysql_do -e "CREATE TABLE IF NOT EXISTS schema_migrations (
  filename VARCHAR(255) PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"

# 원장 초기화 가드(normal 모드): 원장이 비어 있으면 이 DB는 아직 baseline 되지 않은 것이다.
# 여기서 001 부터 재적용하면 가드 없는 비멱등 ALTER(001/002/004/005 의 bare ADD COLUMN 등)가
# 이미 반영된 기존 스키마에서 duplicate-column 으로 배포를 반복 실패시킨다. auto-seed(전부
# "적용됨" 표기)는 DB가 뒤처져 있을 때 미적용분을 조용히 건너뛰어 스키마 드리프트를 낳으므로
# 채택하지 않는다. 최초 도입은 반드시 명시적 `--baseline` 로 사람이 적용 상태를 확인해 초기화한다.
if [[ "$BASELINE" != "1" ]]; then
  ledger_count="$(mysql_do -N -B -e "SELECT COUNT(*) FROM schema_migrations;")"
  if [[ "$ledger_count" == "0" ]]; then
    echo "ERROR: schema_migrations 원장이 비어 있음 — 이 DB는 아직 baseline 되지 않았다." >&2
    echo "       기존 스키마면 적용 상태를 확인한 뒤 'migrate-db.sh --baseline' 을 1회 실행하라." >&2
    echo "       (normal 모드 자동 적용을 막아 duplicate-column 배포 실패 / 스키마 드리프트 방지)" >&2
    exit 1
  fi
fi

# 대상 목록 수집(순번 정렬). macmini /bin/bash 3.2 호환 — mapfile 미사용.
FILES=()
while IFS= read -r _f; do FILES+=("$_f"); done \
  < <(ls -1 "$MIGRATIONS_DIR"/migration_[0-9][0-9][0-9]_*.sql 2>/dev/null | sort)
if (( ${#FILES[@]} == 0 )); then
  echo "no numbered migrations found in $MIGRATIONS_DIR" >&2
  exit 1
fi

applied=0
for path in "${FILES[@]}"; do
  fname="$(basename "$path")"
  # already-applied 체크 — 원장에 있으면 건너뛴다.
  exists="$(mysql_do -N -B -e \
    "SELECT COUNT(*) FROM schema_migrations WHERE filename='$fname';")"
  if [[ "$exists" != "0" ]]; then
    continue
  fi

  if [[ "$BASELINE" == "1" ]]; then
    echo "baseline: mark $fname as applied (not run)"
  else
    echo "applying: $fname"
    mysql_do < "$path"
  fi
  mysql_do -e "INSERT INTO schema_migrations (filename) VALUES ('$fname');"
  applied=$((applied + 1))
done

if [[ "$BASELINE" == "1" ]]; then
  echo "migrate ok: baselined $applied file(s) into schema_migrations"
else
  echo "migrate ok: applied $applied new migration(s)"
fi
