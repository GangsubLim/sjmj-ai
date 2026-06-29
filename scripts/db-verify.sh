#!/usr/bin/env bash
# db-verify.sh — Phase 1A DB 이전 무결성 자동 검증
#
# 무엇을 검증하나:
#   1) row count 일치  — 덤프만 적재(baseline) vs 덤프+migration_007 적재(target)의
#                        원본 8테이블 row count가 정확히 같다(마이그레이션이 데이터를 안 건드림).
#   2) FK 무결성       — target DB의 모든 FK에 고아 행이 없다.
#   3) ML 이음새 존재  — idx_invoices_total_supply 인덱스 + ocr_jobs · ocr_corrections 테이블.
#
# 사용법:
#   SJMJ_DB_BACKUP=/path/to/db-2026-06-24-backup.sql scripts/db-verify.sh
# env:
#   SJMJ_DB_BACKUP  운영 덤프 경로(필수). 미설정 시 SJMJ-Web 기본 경로 시도.
#   MYSQL_USER      기본 root.   MYSQL_PWD  비밀번호(없으면 무암호).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$REPO_ROOT/db"
MIGRATION="$DB_DIR/migration_007_ml_seam.sql"

DUMP="${SJMJ_DB_BACKUP:-$HOME/projects/SJMJ-Web/database/db-2026-06-24-backup.sql}"
MYSQL_USER="${MYSQL_USER:-root}"

DB_BASE="sjmj_ai_verify_baseline"
DB_TARGET="sjmj_ai_verify_target"

# --- mysql 호출 헬퍼 (무암호/암호 모두 지원) ---
MYSQL_ARGS=(-u "$MYSQL_USER")
[ -n "${MYSQL_PWD:-}" ] && MYSQL_ARGS+=(-p"$MYSQL_PWD")
mysql_exec() { mysql "${MYSQL_ARGS[@]}" "$@"; }
mysql_scalar() { mysql "${MYSQL_ARGS[@]}" -N -B "$@"; }

fail() { echo "❌ FAIL: $*" >&2; exit 1; }

[ -f "$DUMP" ] || fail "덤프 없음: $DUMP (SJMJ_DB_BACKUP 설정 필요)"
[ -f "$MIGRATION" ] || fail "마이그레이션 없음: $MIGRATION"

cleanup() {
  mysql_exec -e "DROP DATABASE IF EXISTS \`$DB_BASE\`; DROP DATABASE IF EXISTS \`$DB_TARGET\`;" 2>/dev/null || true
}
trap cleanup EXIT

echo "▶ 덤프: $DUMP"
echo "▶ 마이그레이션: $MIGRATION"

# --- 1) baseline: 덤프만 적재 ---
mysql_exec -e "DROP DATABASE IF EXISTS \`$DB_BASE\`; CREATE DATABASE \`$DB_BASE\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql_exec "$DB_BASE" < "$DUMP"

# --- 2) target: 덤프 + migration_007 적재 ---
mysql_exec -e "DROP DATABASE IF EXISTS \`$DB_TARGET\`; CREATE DATABASE \`$DB_TARGET\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql_exec "$DB_TARGET" < "$DUMP"
mysql_exec "$DB_TARGET" < "$MIGRATION"

# --- 검증 1: 원본 테이블 row count 일치 ---
echo ""
echo "== [1] row count 일치 (baseline vs target) =="
TABLES=$(mysql_scalar -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$DB_BASE' AND table_type='BASE TABLE' ORDER BY table_name;")
mismatch=0
for t in $TABLES; do
  b=$(mysql_scalar -e "SELECT COUNT(*) FROM \`$DB_BASE\`.\`$t\`;")
  g=$(mysql_scalar -e "SELECT COUNT(*) FROM \`$DB_TARGET\`.\`$t\`;")
  if [ "$b" = "$g" ]; then
    printf "   OK   %-22s %s\n" "$t" "$b"
  else
    printf "   DIFF %-22s baseline=%s target=%s\n" "$t" "$b" "$g"
    mismatch=1
  fi
done
[ "$mismatch" -eq 0 ] || fail "row count 불일치 — 마이그레이션이 데이터를 변경함"

# --- 검증 2: FK 무결성 (target의 모든 FK 고아 검사, information_schema 기반 동적 생성) ---
echo ""
echo "== [2] FK 무결성 (고아 행 0건) =="
ORPHAN_CHECKS=$(mysql_scalar "$DB_TARGET" -e "
  SELECT CONCAT(
    'SELECT ''', table_name, '.', column_name, ' -> ', referenced_table_name,
    ''' AS fk, COUNT(*) FROM \`', table_name, '\` c LEFT JOIN \`', referenced_table_name,
    '\` p ON c.\`', column_name, '\` = p.\`', referenced_column_name,
    '\` WHERE c.\`', column_name, '\` IS NOT NULL AND p.\`', referenced_column_name, '\` IS NULL;')
  FROM information_schema.key_column_usage
  WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL;")
fk_bad=0
if [ -z "$ORPHAN_CHECKS" ]; then
  echo "   (FK 정의 없음)"
else
  while IFS=$'\t' read -r fk orphans; do
    [ -z "$fk" ] && continue
    if [ "$orphans" -eq 0 ]; then printf "   OK   %-40s %s\n" "$fk" "$orphans"
    else printf "   ORPHAN %-38s %s\n" "$fk" "$orphans"; fk_bad=1; fi
  done < <(while IFS= read -r q; do [ -n "$q" ] && mysql_scalar "$DB_TARGET" -e "$q"; done <<< "$ORPHAN_CHECKS")
fi
[ "$fk_bad" -eq 0 ] || fail "FK 고아 행 발견"

# --- 검증 3: ML 이음새 존재 ---
echo ""
echo "== [3] ML 이음새 존재 =="
idx=$(mysql_scalar -e "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='$DB_TARGET' AND table_name='invoices' AND index_name='idx_invoices_total_supply';")
[ "$idx" -ge 1 ] && echo "   OK   idx_invoices_total_supply" || fail "idx_invoices_total_supply 없음"
for tbl in ocr_jobs ocr_corrections; do
  ex=$(mysql_scalar -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_TARGET' AND table_name='$tbl';")
  cnt=$(mysql_scalar -e "SELECT COUNT(*) FROM \`$DB_TARGET\`.\`$tbl\`;")
  [ "$ex" -eq 1 ] && echo "   OK   $tbl (행 $cnt, 빈 테이블 기대)" || fail "$tbl 테이블 없음"
done

echo ""
echo "✅ PASS — row count 일치 · FK 무결성 · ML 이음새 모두 통과"
