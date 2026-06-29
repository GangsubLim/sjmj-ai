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
