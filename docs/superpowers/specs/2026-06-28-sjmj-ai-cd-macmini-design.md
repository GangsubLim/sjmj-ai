# sjmj-ai CD 구성 설계 — macmini 태그 배포 + release 워크플로우

- 작성일: 2026-06-28
- 상태: 설계 확정 (구현 대기)
- 레퍼런스: `~/projects/donboksa`(가장 가까운 구조), `~/projects/augron`(pnpm 모노레포 변형)

## 배경 / 목적

sjmj-ai는 Phase 1D에서 macmini에 launchd 단일 서비스(`ai.sjmj.backend`, :8400)로 수동 배포되어 Tailscale `:8443`으로 운영 중이다. augron·donboksa가 동일 macmini에서 **`v*` 태그 push → self-hosted 러너 → 빌드 → launchd 재시작 → health → 실패 시 롤백** 패턴의 CD를 쓰고 있으므로, sjmj-ai도 **동일한 CD 방식**을 갖추되 현 프로젝트 특성(단일 서비스, 운영 PII DB, 모노레포 경로)에 맞게 적응한다. 또한 각 프로젝트가 보유한 `my-release` 스킬을 sjmj-ai용으로 구현한다.

현재 sjmj-ai에는 `VERSION`/`CHANGELOG`/`.github/workflows`/`.claude/skills`가 전혀 없고, sjmj-ai repo용 self-hosted 러너도 0개다(macmini는 augron/donboksa 러너만 운영).

## donboksa → sjmj-ai 적응 포인트

| 항목 | donboksa | sjmj-ai |
| --- | --- | --- |
| 서비스 구성 | backend(:8200) + frontend(:8300) 2서비스 | 백엔드(:8400)가 프론트 dist까지 서빙 → **단일 서비스** `ai.sjmj.backend` |
| 경로 | `backend/`·`frontend/`(루트) | `apps/invoice-ocr/{backend,frontend}` |
| 외부 노출 | frontend launchd | `tailscale serve --bg --https=8443`(배포 무관, 영속) |
| DB | backup/backfill launchd 별도 | deploy에 **mysqldump 백업 단계** 추가, 마이그레이션은 수동 |
| 러너 | 등록됨 | **신규 등록 필요**(이번 스코프) |
| 운영 클론 | `/Users/submini/donboksa` | `/Users/submini/sjmj-ai`(Phase 1D에서 존재) |

## 결정 사항

- **DB 처리**: deploy 시작 시 mysqldump 백업 단계만 추가한다. 마이그레이션 자동 적용은 하지 않는다(운영 PII에 대한 무중단 자동 DDL 위험 회피). 스키마 변경은 운영자가 `scripts/db-verify.sh`로 검증하며 수동 적용한다.
- **러너 등록**: 이번 스코프에 포함한다(macmini SSH + GH 등록 토큰 + `svc install`).
- **버전 baseline + 단일 진실원**: 루트 `VERSION`을 현재 앱 버전과 일치하는 **`0.1.0`**으로 초기화한다(`app/config.py`의 `APP_VERSION="0.1.0"`·운영 `/health` 응답과 동치). `0.0.0` baseline은 즉시 불일치를 만들므로 채택하지 않는다. `release.sh`가 bump 시 루트 `VERSION`과 `config.py:APP_VERSION`을 **함께** 갱신해 이중성을 제거한다. 첫 CD 릴리스는 여기서 bump(예: `scripts/release.sh patch` → `0.1.1`, "CD 도입").
- **스케줄 백업 launchagent**: 스코프 외(Phase 3). 이번엔 deploy 단계 백업 + 수동 호출 스크립트만.

## 컴포넌트

### 1. 버전 진실원 (donboksa 동일)

- 루트 **`VERSION`** = source of truth. 패키지(`backend`/`frontend`) version 필드는 동기화하지 않는다(publish 없이 git tag로만 배포).
- 루트 **`CHANGELOG.md`** = [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/) 4카테고리(Added/Changed/Fixed/Removed).
- **앱 버전 결합**: `apps/invoice-ocr/backend/app/config.py`의 `APP_VERSION`이 `/health` 응답 버전이다. release.sh가 이 상수를 루트 `VERSION`과 **동기 갱신**한다(둘만 동기, 패키지 version은 무관). 초기값은 둘 다 `0.1.0`으로 일치.
- **버전 테스트 bump-proof**: `tests/test_health.py`가 리터럴 `"0.1.0"` 대신 `from app.config import APP_VERSION`을 import해 비교하도록 변경(버전 bump가 health 테스트를 깨지 않게).

### 2. CI — `.github/workflows/ci.yml`

- 트리거: `pull_request` → `branches: [main, devel]`. `concurrency` cancel-in-progress.
- **lint** (ubuntu): prettier `--check` + ruff `check`/`format --check`(`apps/invoice-ocr/backend` 대상).
- **frontend** (ubuntu, working-directory `apps/invoice-ocr/frontend`): `npm ci` → eslint → tsc typecheck → vitest → build.
- **backend** (ubuntu, working-directory `apps/invoice-ocr/backend`): `uv sync --frozen` → pytest(+coverage ≥80). **실 MySQL 필수**(아래 확정).
- **MySQL service (확정)**: 백엔드 pytest는 `conftest.py:30-36`이 `127.0.0.1:3306`의 `sjmj_test` DB에 접속하고, `_engine` fixture가 `tests/fixtures/schema_test.sql`을 직접 적재한다. 따라서 backend job에 `services.mysql`(이미지 `mysql:8`)을 띄운다:
  - service `env`: `MYSQL_ROOT_PASSWORD: <root pw>`, `MYSQL_DATABASE: sjmj_test`.
  - service `options`: `--health-cmd "mysqladmin ping" --health-interval/--timeout/--retries`로 ready 대기.
  - pytest step `env`: `DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=sjmj_test DB_USER=root DB_PASS=<root pw>` — conftest 기본 user `sjmj_test`를 root로 override(TRUNCATE/DDL 권한 필요).
  - **스키마 init step 불필요** — `_engine` fixture가 빈 `sjmj_test`에 `schema_test.sql`을 적재한다. service는 빈 DB만 제공하면 됨.

### 3. Deploy — `.github/workflows/deploy.yml`

- 트리거: `push.tags: ["v*"]` + `workflow_dispatch`.
- `runs-on: [self-hosted, macmini]`, **`defaults.run.working-directory: /Users/submini/sjmj-ai`**. job 레벨 `working-directory` 키는 GitHub Actions에 존재하지 않는다 — 반드시 `defaults.run.working-directory`(또는 각 `run` step의 `working-directory`)로 명시한다(donboksa `deploy-macmini.yml:12-14` 동일).
- `actions/checkout` 미사용 — 운영 클론을 직접 `git fetch`/`checkout`(donboksa·augron 동일).
- env: 기존 `SJMJ_ENV_FILE`(`/Users/submini/.sjmj-ai/backend.env`), `SJMJ_STATIC_DIR`, health URL `http://127.0.0.1:8400/health`.

스텝 순서:

1. 직전 커밋 SHA 기록(롤백 앵커).
2. `git fetch --tags --force` → 태그/SHA checkout.
3. **mysqldump 백업** — `scripts/backup-db.sh` 호출(아래 4).
4. 백엔드 `uv sync --frozen` → `uv run pytest -q`(스모크).
5. 프론트 `npm ci` → `npm run build`(dist 갱신; 백엔드가 서빙).
6. **단일 launchd 재시작** — `scripts/install-launchagent.sh`(`ai.sjmj.backend`, bootout→bootstrap→kickstart -k).
7. **health check** — `curl -fsS http://127.0.0.1:8400/health` 30회 retry(2s).
8. **rollback on failure** — `if: failure()`: 직전 SHA checkout → 재빌드 → 재시작 → health 재검증(백업은 3에서 확보).

> tailscale serve `:8443`은 배포가 건드리지 않는다(영속). 운영 앱은 127.0.0.1:8400 유지.

### 4. 신규 스크립트 — `scripts/backup-db.sh`

- donboksa `scripts/backup-db.sh` 적응. `sjmj` DB를 `~/sjmj-backups/sjmj-<YYYYMMDD-HHMMSS>.sql.gz`로 mysqldump.
- 최근 10개 retain(오래된 것 정리).
- DB 접속·**대상 DB명 모두** `~/.sjmj-ai/backend.env`의 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS`에서 읽는다. **타깃 DB명 하드코딩 금지** — 런타임 DB와 백업 DB 발산 방지. Phase 1D 실제 env = root/무비번/127.0.0.1/`DB_NAME=sjmj`.
- deploy 3단계에서 호출 + 운영자 수동 호출 겸용. 멱등/비파괴(읽기 전용 덤프).
- **`deploy/env/backend.env.example` 갱신**: 현 예시는 `DB_*`가 없어 `config.py` 기본값 `kslim/kslim`으로 떨어진다. `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS`(`DB_NAME=sjmj` 안내 포함)를 추가해 런타임 DB를 명시화하고 backup-db.sh와 단일 출처를 공유한다.

### 5. `scripts/release.sh`

- donboksa 이식. 동작: main 브랜치+워킹트리 클린 검증 → origin/main 동기화 검증 → VERSION/태그 계산(`patch|minor|major|x.y.z`) → 태그·release 브랜치 중복 선검사 → **로컬 검증(PR CI 게이트 미러: prettier + ruff(`apps/invoice-ocr/backend`) + eslint/tsc(`apps/invoice-ocr/frontend`))** → `VERSION` + **`app/config.py:APP_VERSION` 동기 갱신** + `CHANGELOG.md` 헤더 prepend → `release/vX.Y.Z` 브랜치 + 커밋.
- 경로만 sjmj 모노레포 구조로 교정. pytest/build는 macmini 러너(deploy)로 이연. `--skip-verify`/`--dry-run` 지원.

### 6. `my-release` 스킬 — `.claude/skills/my-release/SKILL.md` (+ `.agents/skills/my-release/SKILL.md` 미러)

donboksa 9단계 이식, sjmj 특성 반영:

1. 사전 확인(`gh pr list`, `git log main..devel`).
2. 버전 결정(major/minor/patch 표).
3. `scripts/release.sh` 실행.
4. CHANGELOG 본문 작성(사용자 관점 release notes, 카테고리 규칙).
5. push + PR(main) + CI watch + merge.
6. 태그 push(= `deploy.yml` 배포 트리거).
7. devel 동기화.
8. GitHub Release 생성.
9. 배포 확인(`gh run watch`, health `http://127.0.0.1:8400/health` — **단일 서비스**).

sjmj 주석: 단일 서비스(프론트 :8300 없음)·deploy의 mysqldump 백업 단계·tailscale `:8443`·`deleteBranchOnMerge`(sjmj-ai repo 설정 확인 후 반영)·gh auth(`GangsubLim`).

### 7. macmini 러너 등록 (SSH, 이번 스코프)

- `gh api repos/GangsubLim/sjmj-ai/actions/runners/registration-token`로 토큰 발급.
- macmini에서 augron/donboksa와 동일 방식으로 러너 디렉터리 구성 → `./config.sh`(라벨 `self-hosted,macmini`) → `./svc.sh install` → `./svc.sh start`.
- `gh api .../actions/runners`로 online 확인. 운영 클론 `/Users/submini/sjmj-ai` 존재 전제(Phase 1D).

## 데이터 흐름

```
개발(devel) → main PR merge → release.sh(VERSION+CHANGELOG+release 브랜치)
  → release PR → CI(ubuntu) → merge → v0.1.0 태그 push
  → deploy.yml(macmini 러너): 백업 → uv sync+pytest → npm build → launchd 재시작 → health → (실패 시 롤백)
  → devel 동기화 → GitHub Release
```

## 에러 처리 / 안전장치

- deploy health 30회 retry 실패 → 자동 롤백(직전 SHA 재빌드/재시작/재health). mysqldump 백업이 롤백 전 확보됨.
- release.sh: 태그/브랜치(로컬·원격) 중복 fail-fast, origin/main drift 검출, 로컬 lint 게이트.
- CI가 PR에서 동일 검사를 권위 있게 재검(로컬 우회 `--skip-verify`해도 PR에서 막힘).

## 테스트 / 검증 (DoD)

- `ci.yml`이 PR(→main/devel)에서 lint/frontend/backend 잡 통과(backend는 `services.mysql`로 `sjmj_test` 접속, 239 테스트 green).
- `test_health`가 `APP_VERSION` import 기준으로 통과하고, release bump 후 `VERSION`·`config.py:APP_VERSION`·`/health` 버전이 일치.
- `scripts/release.sh 0.1.0` → release PR → merge → `v0.1.0` 태그 push.
- `deploy.yml`이 macmini 러너에서 백업→빌드→재시작→health 통과로 `success`.
- tailscale `https://macmini.tail99e9f1.ts.net:8443`에서 앱 정상(7페이지 + PDF), 운영 데이터 보존.
- `backup-db.sh` 단독 실행 시 `~/sjmj-backups/`에 `.sql.gz` 생성·retain 동작.

## 스코프 외 (Phase 3)

- 스케줄 mysqldump 백업 launchagent, 모니터링/알림, 자동 마이그레이션 적용.
```
