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
>
> - **단일 서비스** `ai.sjmj.backend`(:8400)가 프론트 dist까지 서빙. 별도 frontend 서비스 없음.
> - deploy 시작 시 **mysqldump 백업**(`scripts/backup-db.sh`, 대상 `sjmj` DB) 단계로 롤백 앵커 확보.
> - 외부 노출은 `tailscale serve --https=8443`(영속, 배포와 무관하게 상시 유지).
> - 패키지 version 필드 동기 안 함 — 진실원은 루트 `VERSION` + `config.py:APP_VERSION` 둘뿐. `scripts/sync-version.sh`가 함께 갱신.
> - 모노레포 경로 `apps/invoice-ocr/{backend,frontend}`.
> - **`deleteBranchOnMerge=false`** — PR merge 시 head 브랜치가 자동 삭제되지 **않는다**. `release/vX.Y.Z`는 merge 후 수동 정리.

## 릴리스 흐름 개요

```
devel 작업 → main PR merge → release.sh(VERSION+APP_VERSION bump+CHANGELOG+release 브랜치)
  → release PR → CI → merge → 태그 push(배포) → devel 동기화 → GitHub Release
```

## Step 1: 사전 확인

```bash
git fetch --all --prune --tags

# devel → main 미병합 PR 점검 (GitHub 상태가 로컬보다 정확)
gh pr list --base main --state open
gh pr list --base main --state merged --limit 3
```

- **devel → main 작업이 아직 main에 없으면**: 먼저 commit-push-pr로 devel→main PR을 merge한다.
- **이미 merge됨**: 그대로 진행. release 브랜치는 main에서 생성된다.

```bash
git checkout main && git pull origin main
git log main..devel --oneline   # 빈 결과 = devel이 main보다 앞서지 않음(정상)
```

`git log main..devel`에 결과가 있으면 devel→main PR을 먼저 처리한다.

## Step 2: 버전 결정

현재 버전:

```bash
cat VERSION
```

| 변경 유형                                | bump  | 예시          |
| ---------------------------------------- | ----- | ------------- |
| Breaking change (스키마/API 호환성 깨짐) | major | 0.x → 1.0.0   |
| 기능 추가 (새 화면·API·서비스 등)        | minor | 0.1.x → 0.2.0 |
| 버그 수정·안정성·문서                    | patch | 0.1.0 → 0.1.1 |

- **baseline**: `VERSION`은 `0.1.0`. 첫 CD 릴리스는 patch: `scripts/release.sh 0.1.1` 또는 `scripts/release.sh patch`.
- 사용자가 `v0.2.0`처럼 `v` prefix를 줘도 release.sh가 제거한다. `patch/minor/major`도 가능.

## Step 3: release.sh 실행

```bash
scripts/release.sh <patch|minor|major|x.y.z>
```

스크립트 동작:

1. main 브랜치 + 워킹트리 클린 + origin/main 동기 검증
2. VERSION 읽어 다음 버전 계산 + 태그/브랜치 중복 선검사
3. **로컬 검증 — PR CI 게이트 미러**: `uvx ruff format --check . && uvx ruff check .` (backend) + `npm run lint && npm run format:check` (frontend)
4. `scripts/sync-version.sh`로 루트 `VERSION` + `apps/invoice-ocr/backend/app/config.py:APP_VERSION` 동기 + `CHANGELOG.md`에 `## [vX.Y.Z] — YYYY-MM-DD` 헤더 prepend
5. `release/vX.Y.Z` 브랜치 생성 + 커밋

> pytest·frontend build는 **배포 시 macmini runner**(`deploy.yml`)에서 실행된다. 로컬 검증엔 포함하지 않는다(로컬 conda env에서 uv run pytest가 깨질 수 있는 환경 이슈 회피). 전체 preflight가 필요하면 `cd apps/invoice-ocr/backend && .venv/bin/python -m pytest -q`로 직접 확인한다.
>
> 검증 실패 시 스크립트가 중단된다. 원인 수정 후 재실행. 급할 때 `--skip-verify`로 우회 가능하나 PR CI에서 동일 검사가 다시 막는다.

## Step 4: CHANGELOG 본문 작성

스크립트는 헤더 줄만 박았다. 그 아래에 **사용자 관점 release notes**를 작성한다. commit 메시지를 그대로 옮기지 말고 "이 버전을 받으면 무엇이 달라지는가"를 한 줄 명령형으로 재서술한다.

```markdown
## [vX.Y.Z] — YYYY-MM-DD

이번 릴리스 한 줄 요약 (#PR번호).

### Added

- 새 기능 추가 ([#12](https://github.com/GangsubLim/sjmj-ai/pull/12))

### Changed

- 기존 동작 변경 ([#11](https://github.com/GangsubLim/sjmj-ai/pull/11))

### Fixed

- 버그 수정 (f3b76a2)

### Removed

- 미사용 기능 제거 ([#12](https://github.com/GangsubLim/sjmj-ai/pull/12))
```

규칙:

- 카테고리는 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/) 4개(Added/Changed/Fixed/Removed) 기본. `Deprecated`·`Security`는 해당 시에만. **항목 없는 카테고리는 헤더 생략**.
- commit type → 카테고리: `feat:`→Added, `fix:`/`hotfix:`→Fixed, `refactor:`/`perf:`/`docs:`(동작 영향)→Changed, `BREAKING`/기능 삭제→Removed. `style:`/`test:`/`chore:`/`ci:`/`build:`는 기본 제외(사용자 가시 변경이면 Changed).
- 한 줄 명령형, 마침표 없음, `type(scope):` prefix 본문 노출 금지. 한국어.
- 참조: PR이면 full URL `([#12](.../pull/12))`, 직접 commit이면 short SHA만 `(f3b76a2)`.

참조 추출:

```bash
git log <이전태그>..HEAD --pretty=format:'%H%x09%s' | while IFS=$'\t' read sha subject; do
  pr=$(echo "$subject" | grep -oE '#[0-9]+' | head -1)
  if [ -n "$pr" ]; then echo "[$pr](https://github.com/GangsubLim/sjmj-ai/pull/${pr#\#})"; else git rev-parse --short "$sha"; fi
done
```

(첫 릴리스라 이전 태그가 없으면 `git log --oneline`으로 전체 확인)

작성 후 amend:

```bash
git add CHANGELOG.md && git commit --amend --no-edit
```

## Step 5: Push + PR + CI + Merge

```bash
git push origin release/vX.Y.Z
gh pr create --base main --head release/vX.Y.Z \
  --title "release: vX.Y.Z — 릴리스 설명" \
  --body "릴리스 요약"
```

CI는 GitHub Actions 큐에 들어올 때까지 잠깐 대기가 필요하다. `gh pr checks`가 "no checks"를 즉시 반환하면 아직 시작 전인 것.

```bash
PR_NUM=<PR번호>
for i in $(seq 1 30); do
  CHECKS=$(gh pr checks $PR_NUM 2>&1)
  if echo "$CHECKS" | grep -q "no checks"; then echo "CI 대기 ($i/30)"; sleep 10; else echo "$CHECKS"; break; fi
done
gh pr checks $PR_NUM --watch --interval 10
```

- **모두 pass(ruff/eslint)**: merge.
  ```bash
  gh pr merge <PR번호> --merge
  ```
  > `deleteBranchOnMerge=false`라 `release/vX.Y.Z`가 원격에 남는다. 정리: `git push origin :release/vX.Y.Z` (선택).
- **fail**: 사용자에게 보고 후 승인 대기. 임의 merge 금지.

## Step 6: 태그 부여 → 배포 트리거

> ⚡ `git push origin vX.Y.Z` 실행 즉시 `deploy.yml`이 돌며 **배포가 시작된다**. 이후 Step 7~8은 배포와 병렬.

```bash
git checkout main && git pull origin main
git tag vX.Y.Z "$(git rev-parse HEAD)"
git push origin vX.Y.Z   # ← 이 시점에 deploy.yml 자동 실행
```

## Step 7: devel 동기화

```bash
git checkout devel
git merge main
git push origin devel
```

> sjmj-ai는 `origin/devel`이 자동 삭제되지 않으므로 `-u` 불필요.
>
> conflict(드묾) 시 **`git reset --hard main` 전에 반드시 `git log main..devel --oneline`이 빈 결과인지 확인**한다. 비어 있으면(devel ⊆ main) reset이 안전하다:
>
> ```bash
> git log main..devel --oneline   # 결과 있으면 reset 금지 — 그 커밋이 사라진다
> # 빈 결과일 때만:
> git reset --hard main && git push --force-with-lease origin devel
> ```
>
> 결과가 비어 있지 않으면(devel에 main 미병합 커밋 존재) reset 대신 `git merge main`으로 conflict를 직접 resolve한다.

## Step 8: GitHub Release 생성

```bash
VER=$(cat VERSION)
RELEASE_NOTES=$(awk "/^## \[v${VER}\]/{f=1;next} f&&/^## \[/{exit} f" CHANGELOG.md)
gh release create "v${VER}" --title "v${VER}" --notes "$RELEASE_NOTES"
# 자동 커밋/PR 목록을 원하면 --notes 대신 --generate-notes 단독 사용(둘 동시 사용 시 본문 중복).
```

## Step 9: 배포 확인

태그 push(Step 6)로 트리거된 배포 상태 확인:

```bash
gh run list --workflow=deploy.yml --limit 1
gh run watch <run-id>
```

`deploy.yml`이 수행하는 단계: 이전 커밋 SHA 기록 → 태그 checkout → **mysqldump 백업**(`scripts/backup-db.sh`, `sjmj` DB → `~/sjmj-backups/`) → backend `uv sync --frozen` + import smoke → frontend `npm ci && npm run build` → LaunchAgent(`ai.sjmj.backend`, :8400) 재시작 → health check(`http://127.0.0.1:8400/health`, 30회 retry) → **실패 시 이전 SHA로 자동 rollback**.

> **health 재확인은 보통 불필요하다 — run 결과로 충분하다.** `deploy.yml`의 Health check 스텝이 `:8400/health`를 macmini runner에서 `curl -fsS`로 30회 retry 검증하고, 통과 못 하면 자동 rollback + run failure로 끝난다. 즉 **run이 `success`면 endpoint는 이미 통과한 것**.
>
> 사람이 직접 눈으로 확인하고 싶을 때만 — macmini에서, 또는 macmini에 ssh로 접속해 — 아래를 실행한다:
>
> ```bash
> curl -fsS http://127.0.0.1:8400/health && echo
> ```
>
> **첫 배포 전제** — macmini에 다음이 준비돼 있어야 성공한다: self-hosted runner(`[self-hosted, macmini]`) 온라인, 운영 repo 경로 `/Users/submini/sjmj-ai`, env 파일 `/Users/submini/.sjmj-ai/backend.env`(DB_* 포함), LaunchAgent plist 등록. 미비 시 backup/sync/build/health 단계에서 실패한다.

## gh auth 주의

리모트는 `GangsubLim` 계정 소유. 실행 전 활성 계정 확인:

```bash
gh auth status
gh auth switch --user GangsubLim   # 다른 계정이 활성이면
```

## 긴급 패치 (hotfix)

1. main에서 `hotfix/vX.Y.Z` 브랜치 생성, 수정+테스트
2. main으로 PR → merge
3. `git checkout main && git pull && scripts/release.sh patch`
4. release PR → merge → 태그 push(배포) → devel 동기화

## 트러블슈팅

| 문제                        | 해결                                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `태그 vX.Y.Z 이미 존재`     | `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z` 후 재실행                               |
| release.sh 검증 실패        | 메시지의 도구(ruff/eslint) 출력대로 수정 후 재실행                                               |
| 로컬 `uv run pytest` 깨짐   | conda base env 간섭. `cd apps/invoice-ocr/backend && .venv/bin/python -m pytest -q` 사용         |
| PR conflict                 | release 브랜치에서 `git merge main` 후 resolve                                                   |
| gh auth 계정 불일치         | `gh auth switch --user GangsubLim`                                                                |
| CI가 계속 "no checks"       | CI 워크플로우의 `on.pull_request.branches`(main, devel) 확인                                     |
| deploy.yml 실패             | `gh run view <run-id> --log-failed`로 원인 확인. 첫 배포면 macmini 경로/env/runner 준비 점검     |
| 백업 실패(`backup-db.sh`)   | `/Users/submini/.sjmj-ai/backend.env`에 `DB_NAME=sjmj` 및 `DB_*` 항목 확인                      |
| release/\* 브랜치 누적      | 자동 삭제 안 됨. `git push origin :release/vX.Y.Z` + `git branch -d release/vX.Y.Z`             |
