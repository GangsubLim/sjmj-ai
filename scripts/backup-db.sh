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
