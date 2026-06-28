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
