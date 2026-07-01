# invoice-ocr / backend

FastAPI + SQLAlchemy + MySQL.

> 아키텍처의 "왜/계약" 정본은 레포 루트 `AGENTS.md`의 "백엔드 아키텍처" 섹션과 `.claude/ai-context/api-spec.json`(엔드포인트 SSoT) + `.claude/rules/api-conventions.md`다. 이 문서는 이 디렉터리에서 작업할 때 바로 쓰는 운영 정보만 담는다.

## 디렉터리 지도

```
app/
  main.py          앱 조립 + 전역 에러 핸들러 + SPA static mount(_mount_static)
  config.py        Settings(pydantic-settings). APP_VERSION 상수(루트 VERSION과 동기)
  db.py            Engine + ContextVar conn 바인딩 (transaction()/connection())
  core/            envelope.py(성공 래핑) · errors.py(AppError) · validators.py(fluent 검증)
  routers/         엔드포인트(sync def, threadpool). slice별 1파일 + ocr
  services/        비즈니스 로직 + 트랜잭션 경계. repo는 생성자 주입
  repositories/    데이터 접근. with db.connection()으로 현재 conn 재사용
tests/
  contract/        라우터 계약(요청·응답·상태코드·검증 메시지)
  unit/            service·core 단위(repo는 mock)
  integration/     repository·schema(실 MySQL)
  conftest.py      세션 스키마 생성 + 테스트마다 TRUNCATE 격리
  fixtures/        schema_test.sql
```

> `app/models/`는 현재 비어 있다(ORM 모델 클래스 미사용 — repository가 Core SQL을 직접 발행).

## 명령어

```bash
uv sync                                  # 의존성
uv run pytest -q                         # 전체 (실 MySQL sjmj_test 필요 — 아래)
uv run pytest tests/unit/test_envelope.py            # 단일 파일
uv run pytest tests/unit/test_envelope.py::test_x    # 단일 테스트
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80   # 커버리지 게이트(CI 미러)
uv run ruff check . && uv run ruff format --check .   # 린트/포맷 (CI 게이트)
```

테스트는 실 MySQL `sjmj_test` DB를 요구한다(`tests/conftest.py`). 접속값은 env로 덮는다: `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASS`(기본 `127.0.0.1:3306` / `sjmj_test` / `sjmj_test` / `sjmj_test_pass`). 세션 시작 시 `fixtures/schema_test.sql`로 스키마를 만들고, 테스트마다 모든 테이블 TRUNCATE + `app_settings` 재시드로 격리한다(contextvar 롤백이 아닌 truncate를 쓰는 이유는 conftest 주석 참조).

## 컨벤션 / 함정 (이 디렉터리 특수)

- **요청 흐름은 router → service → repository 3계층.** 라우터 엔드포인트는 `sync def`(threadpool 실행). 입력 검증은 `core.validators.Validator`(fluent), 응답은 `core.envelope`로 래핑.
- **API 라우터는 SPA catch-all보다 먼저 등록**돼야 우선 매칭된다(`main._mount_static`).
- **트랜잭션 경계는 service.** `with db.transaction():`을 열면 내부 repo 호출이 같은 conn·단일 tx에 합류한다. 바인딩 없는 standalone repo 호출은 `engine.begin()`으로 감싸 블록 종료 시 커밋.
- **DB 접속·백업 대상 DB명은 항상 env(`DB_*`)에서 읽는다 — 하드코딩 금지.**
- **`APP_VERSION`은 루트 `VERSION`과 동기**돼야 한다(`test_version_sync.py`가 검증). `scripts/sync-version.sh`가 둘을 함께 갱신.
- **신규 slice 추가 시** invoices/companies/items/settings/salespeople/sales_records/ocr의 router+service+repository 4종 패턴과 `tests/{contract,unit,integration}/` 3종 구조를 그대로 따른다. spec(`api-spec.json`)을 함께 갱신(드리프트 방지).
- **ruff: google docstring(D) + FastAPI `Depends`/`Body`/`File`/`Form` B008 면제.** `tests/**`는 D 면제, `__init__.py`는 F401 면제(pyproject 참조).

- **점진 현대화 트리거.** 슬라이스에 신규 기능·수정이 들어오면 그 슬라이스의 router+service+repository+tests를 목표 컨벤션(루트 `AGENTS.md` 참조)으로 함께 끌어올린다. 손대지 않는 슬라이스는 그대로 둔다. 응답 envelope shape가 불변이라 프론트 동시 수정은 대개 불필요.
- **Pydantic 전환 선결 인프라.** 첫 Pydantic 슬라이스는 `app/core/errors.py`에 `RequestValidationError` 핸들러를 추가해 Pydantic 검증 실패(기본 422)를 400 `{success, error:{code:"VALIDATION_ERROR", message, details:{필드:메시지}}}`로 변환하고, 그 동작을 고정하는 contract 테스트를 함께 둔다. 이후 슬라이스는 이 핸들러를 공유한다.

### 슬라이스 전환 현황

| slice         | 검증 방식 | 비고      |
| ------------- | --------- | --------- |
| invoices      | Validator | 미전환    |
| companies     | Validator | 미전환    |
| items         | Validator | 미전환    |
| settings      | Validator | 미전환    |
| salespeople   | Validator | 미전환    |
| sales_records | Validator | 미전환    |
| ocr           | Validator | 미전환    |
| curation      | Pydantic  | 최초 전환 |

> 슬라이스를 Pydantic으로 전환하면 이 표의 "검증 방식"을 `Pydantic`으로 갱신한다.
