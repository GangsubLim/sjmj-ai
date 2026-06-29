# CLAUDE.md

This file provides guidance to AI Agents when working with code in this repository.

## 개요

`sjmj-ai`는 SJMJ 업무 AI 자동화 플랫폼 모노레포다. 현재 유일한 모듈은 수기 거래명세서 OCR 자동입력(`apps/invoice-ocr`). 빌드 순서는 SP0(스캐폴딩)→SP1(OCR PoC)→SP2(백엔드 Python 재작성)→SP3(프론트 이식)→SP4(검수 UI)→SP5(운영 굳히기)이며, 설계·근거 정본은 `docs/superpowers/specs/`에 있다.

핵심 맥락: 백엔드와 프론트엔드는 기존 PHP 프로젝트(SJMJ-Web)를 **동형 포팅**한 것이다. 코드 주석의 "PHP ... 동형"은 원본과의 동치 계약을 뜻한다 — 검증 메시지 문자열, 응답 포맷, 부수효과(usage_count 증가 등)까지 골든 테스트로 보존된다. 동작을 바꿀 때는 이 동치 계약을 깨는지 먼저 확인한다.

## 디렉터리 지도

```
apps/invoice-ocr/
  backend/    FastAPI + SQLAlchemy + MySQL (PHP 백엔드 포팅, SP2~)
  frontend/   React 19 + Vite + Tailwind v4 + shadcn (SJMJ-Web frontend 이식, SP3~)
  ml/         수기 OCR 파이프라인 (SP1)
db/           운영 MySQL 스키마 + migration_*.sql (Phase 1A 이전 + ML 이음새)
deploy/       launchd plist 템플릿 + backend.env.example
scripts/      release / sync-version / run-backend / backup-db / install-launchagent / db-verify
docs/superpowers/  specs(설계 정본) · plans · runbooks · research
.github/workflows/  ci.yml(PR 게이트) · deploy.yml(태그→macmini 배포)
VERSION       버전 진실원(single source of truth)
```

## 명령어

루트 Makefile은 `apps/invoice-ocr`의 backend+frontend를 묶는다.

```bash
make install   # backend(uv sync) + frontend(npm install)
make dev       # FastAPI(:8400, --reload) + Vite(:5173) 동시 기동
make build     # 프론트 빌드 → frontend/dist (FastAPI가 동일출처 서빙)
make test      # 백엔드 pytest -q
```

### 백엔드 (`apps/invoice-ocr/backend`)

```bash
uv sync                                  # 의존성
uv run pytest -q                         # 전체 테스트 (실 MySQL sjmj_test 필요 — 아래 참조)
uv run pytest tests/unit/test_envelope.py            # 단일 파일
uv run pytest tests/unit/test_envelope.py::test_x    # 단일 테스트
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80   # 커버리지 게이트(CI 미러)
uv run ruff check . && uv run ruff format --check .   # 린트/포맷 (CI 게이트)
```

테스트는 실 MySQL `sjmj_test` DB를 요구한다(`tests/conftest.py`). 기본 접속값은 env로 덮는다: `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASS` (기본 `127.0.0.1:3306` / `sjmj_test` / `sjmj_test` / `sjmj_test_pass`). 세션 시작 시 `fixtures/schema_test.sql`로 스키마를 만들고, 테스트마다 모든 테이블 TRUNCATE + `app_settings` 재시드로 격리한다(contextvar 롤백이 아닌 truncate를 쓰는 이유는 conftest 주석 참조).

### 프론트엔드 (`apps/invoice-ocr/frontend`)

```bash
npm run dev            # Vite (:5173). /api 는 vite proxy로 :8400 FastAPI에 붙음
npm run build          # tsc -b && vite build → dist
npm run lint           # eslint (CI 게이트)
npm run format:check   # prettier (CI 게이트)
npm run test           # vitest run (단위)
npm run test:e2e       # playwright (라이브 백엔드 필요)
```

API 동작은 env로 제어: `VITE_API_URL`(`/api`), `VITE_API_MODE`(`modern`), `VITE_USE_MOCK`. dev는 vite proxy, prod는 backend가 dist+/api를 동일출처로 서빙한다.

## 백엔드 아키텍처

**API 표면 정본**: `.claude/ai-context/api-spec.json`(OpenAPI 3.0 + `x-api-overview` 한 줄 스캔, 32개 엔드포인트 SSoT)과 규약 `.claude/rules/api-conventions.md`. 라우터/엔드포인트를 만지기 전에 spec을 먼저 읽고, 변경 시 spec을 함께 갱신한다(드리프트 방지).

요청 흐름은 **router → service → repository** 3계층이다. PHP의 Controller/Service/Repository 동형.

- **router** (`app/routers/`): 엔드포인트는 `sync def`(threadpool 실행). 입력 검증은 `core/validators.Validator`(fluent, 골든 메시지 보존), 응답은 `core/envelope`로 래핑. API 라우터는 SPA catch-all(`main._mount_static`)보다 **먼저** 등록돼야 우선 매칭된다.
- **service** (`app/services/`): 비즈니스 로직 + 트랜잭션 경계. `with db.transaction():`으로 tx를 열면 내부 repo 호출이 같은 conn을 공유한다. 의존 repo는 생성자 주입(테스트 mock).
- **repository** (`app/repositories/`): `with db.connection():`으로 현재 바인딩된 conn 재사용, 없으면 엔진에서 새 tx.

**DB 연결 모델** (`app/db.py`): 모듈 전역 `Engine` + `ContextVar`로 conn 바인딩. `transaction()`이 conn을 바인딩하면 그 안의 `connection()` 호출은 모두 같은 단일 tx에 합류한다(PHP PDO/Database 싱글톤 동형). 바인딩이 없는 standalone repo 호출은 `engine.begin()`으로 감싸 블록 종료 시 커밋된다(SQLAlchemy 2.0 Connection은 기본 비-autocommit이므로). 테스트는 `set_test_engine`/`reset_engine`으로 엔진을 교체한다.

**응답 계약** (`app/core/`):

- `envelope.py`: 성공 응답 `{"success": true, "data": ..., "pagination"?: ...}`. DB 행은 `jsonable_encoder`로 직렬화(date→`YYYY-MM-DD`, Decimal 처리).
- `errors.py`: `AppError(status, code, message, details)` → `{"success": false, "error": {...}}`. `bad_request`/`not_found` 헬퍼. 전역 핸들러는 `register_error_handlers`로 등록.
- `validators.py`: 검증 실패 시 `bad_request`로 `details`(필드별 메시지 dict)를 던진다.

`app/config.py`의 `Settings`(pydantic-settings)는 환경변수 경계 검증. 빈 비밀번호(`""`)도 유효값으로 존중. `APP_VERSION` 상수는 루트 `VERSION`과 동기되어야 한다(아래 릴리스 참조, `test_version_sync.py`가 검증).

신규 도메인(슬라이스)을 추가할 때는 기존 slice — invoices/companies/items/settings/salespeople/sales_records — 의 router+service+repository 4종 패턴과 `tests/{contract,unit,integration}/` 3종 테스트 구조를 그대로 따른다.

## ML 파이프라인

`apps/invoice-ocr/ml/`의 핵심: 코어는 paddle-free 경량(pillow만), ML 의존은 `[ml]` extra. 모든 DTO는 `@dataclass(frozen=True)`이고 normalize/validate/score/assemble은 순수함수다. 모델은 어댑터(Protocol) 뒤에 숨겨 지연 로딩하므로 테스트는 합성 데이터 + Fake 어댑터로 paddle 없이 돈다. 데이터·DB·산출물은 전부 gitignore이며 경로는 `.env`(`SJMJ_DATA_DIR`/`SJMJ_DB_BACKUP`)로만 주입한다(하드코딩 금지). 상세 규약은 `ml/`의 자체 지침에 있으며 ml/ 작업 시 자동 주입된다.

## 버전·릴리스·배포

- **버전 진실원은 루트 `VERSION`** 이며 `backend/app/config.py:APP_VERSION`과 동기되어야 한다. `scripts/sync-version.sh <x.y.z>`가 둘을 함께 갱신한다(패키지 `pyproject.toml`/`package.json` version 필드는 건드리지 않는다).
- `scripts/release.sh <patch|minor|major|x.y.z>`: main 클린 검증 → 로컬 CI 미러 → 버전 동기 + CHANGELOG → `release/vX.Y.Z` 브랜치/커밋. 이후 PR·머지·태깅은 `/my-release` 스킬이 안내한다(버전 발행 맥락에서 사용).
- **CI** (`ci.yml`, PR→main/devel): ruff(backend) + eslint/prettier/vitest/build(frontend) + pytest 커버리지 80%(backend, MySQL 서비스 컨테이너).
- **CD** (`deploy.yml`): `v*` 태그 push → self-hosted macmini 러너에서 배포 — 운영 DB 백업 → import 스모크 → 프론트 빌드 → launchd 재시작 → health check → 실패 시 자동 롤백. 운영 백엔드는 launchd(`ai.sjmj.backend`)로 :8400에서 구동(`scripts/install-launchagent.sh`).

## 작업 시 주의

- PHP 동형 코드는 동치 계약(메시지 문자열·응답 포맷·부수효과)을 골든 테스트로 보존한다. 변경 전 해당 계약을 깨는지 확인한다.
- DB 접속·백업 대상 DB명은 항상 env(`DB_*`)에서 읽는다 — 하드코딩 금지(런타임/백업 DB 발산 방지).
- `docs/superpowers/specs/`가 설계 정본이다. 아키텍처 결정의 "왜"는 코드가 아니라 여기에 있다.
