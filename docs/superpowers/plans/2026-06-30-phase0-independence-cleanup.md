# Phase 0 — 독자 노선 baseline 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원본(PHP SJMJ-Web) 결부 출처 언급을 backend/frontend/api-spec에서 0으로 만들고, frontend legacy API 분기를 제거하며, 목표 FastAPI 컨벤션·점진 전환 규칙을 명문화한다 — 전부 **동작 불변**.

**Architecture:** 3개 리뷰 단위(PR)로 분리한다. PR1=backend 출처 세탁(주석/docstring only), PR2=frontend legacy 제거(dead-code + mock 형태 단일화), PR3=api-spec 정리 + 명문화. 각 태스크는 기존 테스트 스위트를 회귀 가드로 삼아 green을 확인하고 독립 커밋한다(신규 동작이 없으므로 RED→GREEN TDD가 아니라 "변경→기존 게이트 green→commit" 사이클).

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy Core / pytest / ruff (backend), React 19 / Vite / TypeScript / vitest (frontend).

## Global Constraints

- **동작 불변이 최우선.** Phase 0의 어떤 태스크도 런타임 동작·API 응답·테스트 기대값을 바꾸지 않는다(예외: PR2 Task 5의 mock 형태 단일화는 mock 전용 fallback seam을 제거하되 hook 동작과 테스트 단언값 `total===1`은 보존). 의심되면 멈추고 보고.
- **DB 접속·백업 대상 DB명은 항상 env(`DB_*`)에서 읽는다 — 하드코딩 금지.** (이 plan은 DB 코드를 만지지 않지만 위반 발견 시 손대지 말 것.)
- **금지 토큰(세탁 대상):** `PHP` · `동형` · `동치` · `SJMJ-Web` · `Controller.php`/`*.php`(소스 식별자 의미) · `Response::` · `PDO` · `골든`/`golden` · `TestData ... 포팅`. Phase 0 종료 시 backend `app/`·`tests/`·`api-spec.json`에서 이들 토큰이 0이어야 한다(아래 "보존 예외" 제외).
- **보존 예외(오탐 — 손대지 말 것):**
  - `app/routers/invoices.py:129` `"원본 거래명세서를 찾을 수 없습니다."` — 복제 원본을 가리키는 사용자 메시지.
  - `tests/contract/test_salespeople_routes.py:72` `name="원본"` — 테스트 입력 데이터.
  - `app/config.py:39` `"""캐시된 Settings 싱글톤을 반환한다."""` — `lru_cache` 서술이지 PHP 결부 아님.
- **커밋 메시지:** conventional commits(한국어 설명). 각 커밋은 아래 trailer로 끝낸다:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
  ```
- **pre-commit 훅:** prettier(markdown/ts 재포맷)·ruff가 돈다. 커밋 거부 시 훅이 수정한 파일을 `git add` 후 재커밋.
- **이 plan의 범위 밖(혼동 주의):** `RequestValidationError` 핸들러 추가, Validator→Pydantic 전환, 검증 status 422 전환은 **Phase 0이 아니다** — 첫 Pydantic 슬라이스(standard pipeline)에서 다룬다. spec `docs/superpowers/specs/2026-06-30-fastapi-convention-modernization-design.md` §점진 전환·§외부 계약 불변식 참조.

---

## PR1 — backend 출처 세탁 (Task 1–2)

주석/docstring만 바꾼다. 코드 식별자·로직·테스트 단언은 불변. 검증 게이트는 `ruff` + 전체 `pytest`.

> **세탁 변환 규칙(모든 사이트 공통):** 금지 토큰을 담은 구절을 제거하되 문장을 문법적으로 유지한다. 패턴별:
>
> - `"""X — PHP .../Y.php 동형."""` → `"""X."""` (대시 이후 결부 절 통째 제거)
> - `"""X — PHP .../Y.php 동형(부가설명)."""` → `"""X — 부가설명."""` (결부만 빼고 부가설명 보존)
> - 문장 중간의 `(PHP ... 동형/동치)` 괄호절 → 괄호째 제거
> - 흐르는 문장 안에 박힌 비교절(예: "PHP PDO와 동등하게") → 결부 제거 후 자족 문장으로 재서술
> - `골든` → 문맥에 따라 `계약` 또는 `회귀`(검증 메시지·응답·부수효과를 고정한다는 의미는 유지)

### Task 1: backend `app/` docstring·주석 세탁

**Files (Modify, docstring/주석 라인만):**

- `app/__init__.py:1`
- `app/config.py:5`
- `app/core/__init__.py:1`
- `app/core/envelope.py:1`
- `app/core/errors.py:1`
- `app/core/validators.py:1,10`
- `app/db.py:3,50`
- `app/routers/invoices.py:1,3,28`
- `app/routers/companies.py:1,3`
- `app/routers/items.py:1,3`
- `app/routers/salespeople.py:1,3`
- `app/routers/settings.py:1`
- `app/routers/sales_records.py:1,5,33`
- `app/services/invoice_service.py:1,3,4`
- `app/services/companies_service.py:1,15`
- `app/services/items_service.py:1,16`
- `app/services/salespeople_service.py:1`
- `app/services/sales_records_service.py:1`
- `app/services/settings_service.py:1,3`
- `app/services/export_service.py:1,3,39`
- `app/repositories/invoice_repository.py:1`
- `app/repositories/companies_repository.py:1`
- `app/repositories/items_repository.py:1,4`
- `app/repositories/salespeople_repository.py:1,19`
- `app/repositories/sales_records_repository.py:1`
- `app/repositories/settings_repository.py:1`

**Interfaces:**

- Consumes: 없음 (주석 전용 변경)
- Produces: 없음 (코드 식별자·시그니처 불변)

- [ ] **Step 1: 단일 라인 docstring 세탁 (확정 치환)**

아래는 정확한 old→new. 각 라인은 파일에서 유일하므로 안전하게 치환한다.

| 파일:라인                                        | OLD (verbatim)                                                                                | NEW                                                                                  |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `app/__init__.py:1`                              | `"""거래명세서 OCR 백엔드 애플리케이션 패키지(SJMJ-Web PHP 동형 포팅)."""`                    | `"""거래명세서 OCR 백엔드 애플리케이션 패키지."""`                                   |
| `app/config.py:5`                                | `존중(PHP config/app.php DB_* 동치). ML 이음새(SJMJ_DATA_DIR·SJMJ_DB_BACKUP)는`               | `존중한다. ML 이음새(SJMJ_DATA_DIR·SJMJ_DB_BACKUP)는`                                |
| `app/core/__init__.py:1`                         | `"""응답 envelope·에러·검증 등 PHP 동형 핵심 계약 모듈."""`                                   | `"""응답 envelope·에러·검증 등 핵심 계약 모듈."""`                                   |
| `app/core/envelope.py:1`                         | `"""구조화 성공 응답 — PHP Response:: 동형.`                                                  | `"""구조화 성공 응답.`                                                               |
| `app/core/errors.py:1`                           | `"""구조화 에러 — PHP Response::error / HttpResponseException 동형."""`                       | `"""구조화 에러 응답."""`                                                            |
| `app/core/validators.py:1`                       | `"""Validator — PHP core/Validator.php 동형(fluent, 메시지 문자 동치)."""`                    | `"""Validator — fluent 입력 검증기."""`                                              |
| `app/core/validators.py:10`                      | `    """입력값을 fluent 체인으로 검증하는 검증기(PHP core/Validator.php 동형)."""`            | `    """입력값을 fluent 체인으로 검증하는 검증기."""`                                |
| `app/db.py:3`                                    | `PHP Database 싱글톤 동형. repository는 `with connection() as conn:`으로 현재`                | `repository는 `with connection() as conn:`으로 현재`                                 |
| `app/routers/invoices.py:1`                      | `"""invoices 라우터 — PHP controllers/InvoiceController.php 동형.`                            | `"""invoices 라우터.`                                                                |
| `app/routers/invoices.py:3`                      | `검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는`              | `검증은 Validator(details 형태 contract 고정), 응답은 구조화 envelope. 엔드포인트는` |
| `app/routers/invoices.py:28`                     | `    # create/update 시 거래처·품목 usage_count 증가(PHP modern 부수효과 동치)`               | `    # create/update 시 거래처·품목 usage_count 증가(부수효과 — contract 고정)`      |
| `app/routers/companies.py:1`                     | `"""companies 라우터 — PHP controllers/CompanyController.php 동형.`                           | `"""companies 라우터.`                                                               |
| `app/routers/companies.py:3`                     | `검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는`              | `검증은 Validator(details 형태 contract 고정), 응답은 구조화 envelope. 엔드포인트는` |
| `app/routers/items.py:1`                         | `"""items 라우터 — PHP controllers/ItemController.php 동형.`                                  | `"""items 라우터.`                                                                   |
| `app/routers/items.py:3`                         | `검증은 Validator(골든 details 형태 보존), 응답은 구조화 envelope. 엔드포인트는`              | `검증은 Validator(details 형태 contract 고정), 응답은 구조화 envelope. 엔드포인트는` |
| `app/routers/salespeople.py:1`                   | `"""salespeople 라우터 — PHP controllers/SalespersonController.php 동형.`                     | `"""salespeople 라우터.`                                                             |
| `app/routers/salespeople.py:3`                   | `index는 PHP 컨트롤러처럼 가짜 pagination(page=1, limit=total=count, totalPages=1)을`         | `index는 가짜 pagination(page=1, limit=total=count, totalPages=1)을`                 |
| `app/routers/settings.py:1`                      | `"""settings 라우터 — PHP controllers/SettingsController.php 동형.`                           | `"""settings 라우터.`                                                                |
| `app/routers/sales_records.py:1`                 | `"""sales_records 라우터 — PHP controllers/SalesRecordController.php 동형.`                   | `"""sales_records 라우터.`                                                           |
| `app/routers/sales_records.py:5`                 | `검증은 Validator(골든 details·메시지 보존), 에러는 bad_request/not_found.`                   | `검증은 Validator(details·메시지 contract 고정), 에러는 bad_request/not_found.`      |
| `app/services/companies_service.py:1`            | `"""CompanyService — PHP services/CompanyService.php 동형.`                                   | `"""CompanyService.`                                                                 |
| `app/services/companies_service.py:15`           | `    """거래처 비즈니스 로직 — PHP CompanyService 동형."""`                                   | `    """거래처 비즈니스 로직."""`                                                    |
| `app/services/items_service.py:1`                | `"""ItemService — PHP services/ItemService.php 동형.`                                         | `"""ItemService.`                                                                    |
| `app/services/items_service.py:16`               | `    """품목 도메인 비즈니스 로직 — PHP ItemService.php 동형."""`                             | `    """품목 도메인 비즈니스 로직."""`                                               |
| `app/services/salespeople_service.py:1`          | `"""SalespersonService — PHP services/SalespersonService.php 동형.`                           | `"""SalespersonService.`                                                             |
| `app/services/sales_records_service.py:1`        | `"""SalesRecordService — PHP services/SalesRecordService.php 동형.`                           | `"""SalesRecordService.`                                                             |
| `app/services/settings_service.py:1`             | `"""SettingsService — PHP services/SettingsService.php 동형.`                                 | `"""SettingsService.`                                                                |
| `app/services/export_service.py:1`               | `"""ExportService — PHP services/ExportService.php 동형(CSV + formula injection 방지).`       | `"""ExportService — CSV 내보내기 + formula injection 방지.`                          |
| `app/services/export_service.py:39`              | `    """거래명세서를 CSV로 내보내는 서비스 — PHP ExportService 동형."""`                      | `    """거래명세서를 CSV로 내보내는 서비스."""`                                      |
| `app/repositories/invoice_repository.py:1`       | `"""InvoiceRepository — PHP repositories/InvoiceRepository.php 동형(text() raw SQL).`         | `"""InvoiceRepository — text() raw SQL 직접 발행.`                                   |
| `app/repositories/companies_repository.py:1`     | `"""CompanyRepository — PHP repositories/CompanyRepository.php 동형(text() raw SQL).`         | `"""CompanyRepository — text() raw SQL 직접 발행.`                                   |
| `app/repositories/items_repository.py:1`         | `"""ItemRepository — PHP repositories/ItemRepository.php 동형(text() raw SQL).`               | `"""ItemRepository — text() raw SQL 직접 발행.`                                      |
| `app/repositories/items_repository.py:4`         | `usage_count·last_used는 기본 DESC, 그 외는 ASC(PHP 동치).`                                   | `usage_count·last_used는 기본 DESC, 그 외는 ASC.`                                    |
| `app/repositories/salespeople_repository.py:1`   | `"""SalespersonRepository — PHP repositories/SalespersonRepository.php 동형(text() raw SQL).` | `"""SalespersonRepository — text() raw SQL 직접 발행.`                               |
| `app/repositories/salespeople_repository.py:19`  | `    """salespeople 테이블 데이터 접근(PHP SalespersonRepository 동형)."""`                   | `    """salespeople 테이블 데이터 접근."""`                                          |
| `app/repositories/sales_records_repository.py:1` | `"""SalesRecordRepository — PHP repositories/SalesRecordRepository.php 동형(text() raw SQL).` | `"""SalesRecordRepository — text() raw SQL 직접 발행.`                               |
| `app/repositories/settings_repository.py:1`      | `"""SettingsRepository — PHP repositories/SettingsRepository.php 동형(text() raw SQL).`       | `"""SettingsRepository — text() raw SQL 직접 발행.`                                  |

- [ ] **Step 2: 흐르는 문장(여러 줄 docstring) 세탁 — 블록 먼저 읽고 재서술**

다음 5개 사이트는 문장이 여러 줄에 걸쳐 흐르므로 `Read`로 해당 docstring 블록 전체를 먼저 본 뒤, 금지 토큰을 제거하고 자족적 문장으로 재서술한다. 의미(무엇을 왜 하는지)는 보존한다.

- `app/db.py:50` — `, PHP PDO(문장별 autocommit)와 동등하게` 비교절을 제거하고, "standalone 호출은 `engine.begin()`이 블록 종료 시 커밋한다"는 의미를 자족 문장으로.
- `app/routers/sales_records.py:33` — `"""PHP is_int || (is_numeric && (string)(int)x === (string)x) 동형.` → 결부를 빼고 "정수 또는 정수 문자열만 허용(bool 제외)"의 의미로 재서술.
- `app/services/invoice_service.py:1,3,4` — line 1 `"""InvoiceService — PHP services/InvoiceService.php 동형.` → `"""InvoiceService.`; line 3~4의 `(골든 InvoiceServiceTest 동치)`, `increment_usage_by_name 규약만` 주변 결부 제거하되 "company_repo/item_repo는 생성자 주입(테스트 mock)" 의미 보존.
- `app/services/settings_service.py:3` — `uploadStamp의 파일 저장은 PHP core/FileUpload.php 동형(issuer 확인 → 크기·MIME` → `uploadStamp의 파일 저장 흐름(issuer 확인 → 크기·MIME` (결부만 제거, 절차 설명 보존).
- `app/services/export_service.py:3` — `설계 차이: PHP는 php://output 직접 출력. FastAPI는 (filename, csv_bytes)를 반환하고` → PHP 비교를 빼고 "이 서비스는 (filename, csv_bytes) 튜플을 반환한다(스트리밍은 라우터 책임)" 류로 재서술.

- [ ] **Step 3: 잔여 금지 토큰 0 확인**

```bash
cd apps/invoice-ocr/backend
grep -rn -iE "php|동형|동치|골든|golden|sjmj-web|response::|[^a-z]pdo[^a-z]" app | grep -vE "invoices\.py:129|config\.py:39"
```

Expected: 빈 출력(=잔여 0). `원본`(invoices.py:129)·`싱글톤`(config.py:39)은 보존 예외이므로 grep 패턴에 없음.

- [ ] **Step 4: 린트·포맷·테스트 게이트 green**

```bash
cd apps/invoice-ocr/backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

Expected: ruff 통과, pytest 전부 PASS(주석 변경이므로 단언값 불변). MySQL `sjmj_test` 미가용 환경이면 최소 `uv run python -c "import app.main"` import 스모크 + ruff만 통과시키고, 그 사실을 커밋 본문에 명시.

- [ ] **Step 5: 커밋**

```bash
cd apps/invoice-ocr/backend
git add app/
git commit -m "$(cat <<'EOF'
docs(backend): app/ docstring·주석에서 원본 결부 출처 세탁

PHP/동형/동치/골든 등 원본 결부 표현을 중립 서술로 교체(동작 불변).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

### Task 2: backend `tests/` docstring 세탁

**Files (Modify, docstring 라인만):**

- `tests/conftest.py:1`
- `tests/unit/test_companies_service.py:1`
- `tests/unit/test_config.py:19`
- `tests/contract/test_companies_routes.py:1`
- `tests/integration/test_companies_repository.py:1`
- `tests/fixtures/test_data.py:1`
- `tests/fixtures/companies_data.py:1`
- `tests/fixtures/items_data.py:1`
- `tests/fixtures/salespeople_data.py:1`
- `tests/fixtures/sales_records_data.py:1`
- `tests/fixtures/settings_data.py:1`

**Interfaces:**

- Consumes: 없음 (주석 전용)
- Produces: 없음 (픽스처 함수·데이터 값 불변)

- [ ] **Step 1: 확정 치환**

| 파일:라인                                          | OLD (verbatim)                                                                                     | NEW                                                                   |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `tests/conftest.py:1`                              | `"""실DB 골든 하니스 — sjmj_test MySQL + truncate 격리 + TestClient.`                              | `"""실DB 계약 하니스 — sjmj_test MySQL + truncate 격리 + TestClient.` |
| `tests/unit/test_companies_service.py:1`           | `"""service 골든 — PHP CompanyServiceTest 동치(mock repo)."""`                                     | `"""CompanyService 계약 테스트(mock repo)."""`                        |
| `tests/unit/test_config.py:19`                     | `    # 빈 비밀번호는 유효한 값 — 미설정과 구분(AppConfig 골든 동치)`                               | `    # 빈 비밀번호는 유효한 값 — 미설정과 구분`                       |
| `tests/contract/test_companies_routes.py:1`        | `"""contract 골든 — TestClient + 실DB. PHP CompanyControllerTest 동치."""`                         | `"""companies 라우터 contract 테스트 — TestClient + 실DB."""`         |
| `tests/integration/test_companies_repository.py:1` | `"""실DB 골든 — PHP CompanyRepositoryTest 동치 이식."""`                                           | `"""CompanyRepository 통합 테스트 — 실DB."""`                         |
| `tests/fixtures/test_data.py:1`                    | `"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php 포팅."""`                              | `"""테스트 입력 팩토리."""`                                           |
| `tests/fixtures/companies_data.py:1`               | `"""companies 골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php::company 포팅."""`           | `"""companies 테스트 입력 팩토리."""`                                 |
| `tests/fixtures/items_data.py:1`                   | `"""items 골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData::item() 포팅."""`                    | `"""items 테스트 입력 팩토리."""`                                     |
| `tests/fixtures/salespeople_data.py:1`             | `"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData::salesperson 포팅."""`                     | `"""salespeople 테스트 입력 팩토리."""`                               |
| `tests/fixtures/sales_records_data.py:1`           | `"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php(salesperson/salesRecord) 포팅."""`     | `"""sales_records 테스트 입력 팩토리."""`                             |
| `tests/fixtures/settings_data.py:1`                | `"""settings 골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php(issuer/appSettings) 포팅."""` | `"""settings 테스트 입력 팩토리."""`                                  |

- [ ] **Step 2: 잔여 금지 토큰 0 확인 (보존 예외 제외)**

```bash
cd apps/invoice-ocr/backend
grep -rn -iE "php|동형|동치|골든|golden|sjmj-web|response::" tests | grep -v "test_salespeople_routes.py:72"
```

Expected: 빈 출력. (`name="원본"`은 `원본`이라 이 패턴에 안 걸리지만, 혹시 다른 매치가 보이면 보존 예외인지 확인.)

- [ ] **Step 3: 테스트 게이트 green**

```bash
cd apps/invoice-ocr/backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

Expected: 전부 PASS(픽스처 값·단언 불변).

- [ ] **Step 4: 커밋**

```bash
cd apps/invoice-ocr/backend
git add tests/
git commit -m "$(cat <<'EOF'
docs(backend): tests/ docstring에서 원본 결부 출처 세탁

골든/포팅/동치 류 결부 표현을 중립 서술로 교체(픽스처 값·단언 불변).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

---

## PR2 — frontend legacy 제거 (Task 3–5)

순서 중요: Task 3(api.ts 분기 제거)→Task 4(mock nested 이전)→Task 5(type flat 필드 제거 + hook fallback + 테스트). 이 순서라야 각 커밋이 tsc/vitest green을 유지한다(flat 호환 필드를 마지막에 제거). 검증 게이트는 `npm run lint && npm run build && npm run test`(모두 `apps/invoice-ocr/frontend`에서).

### Task 3: `api.ts` legacy 분기·`API_MODE` 제거 (modern 단일화)

**Files:**

- Modify: `apps/invoice-ocr/frontend/src/services/api.ts`

**Interfaces:**

- Consumes: `ListResponse`/`SingleResponse` (`@/types/api`) — 형태 불변
- Produces: 동일한 export 표면(`invoiceAPI`/`companySuggestionsAPI`/`itemSuggestionsAPI`/`settingsAPI`/`salespersonAPI`/`salesRecordAPI`/`ocrAPI`/default `api`) — 시그니처 불변, 내부 분기만 제거

> 배경(spec 리뷰 #3): 현재 `API_MODE`는 `?? "legacy"` 기본값이고 legacy 경로는 `/${resource}.php`다. FastAPI 백엔드엔 `.php` 라우트가 없어 legacy는 이미 404(죽은 경로). `.env`/`.env.example`/`.env.production` 모두 `VITE_API_MODE=modern` → 제거는 실배포 무영향. 이 태스크는 modern 경로를 유일 경로로 하드코딩한다.

- [ ] **Step 1: `API_MODE` 선언·`ep()` 헬퍼 제거, 경로 직접화**

`src/services/api.ts:22-24`의 `API_MODE` 선언과 `36-38`의 `ep()`를 제거한다. 이후 모든 `ep("x")` 호출을 리터럴 `"/x"`로 바꾼다. 구체 치환:

Remove (`:22-24`):

```ts
const API_MODE = (import.meta.env.VITE_API_MODE ?? "legacy") as
  | "legacy"
  | "modern";
```

Remove (`:36-38`):

```ts
/** Returns endpoint path based on API_MODE */
const ep = (resource: string): string =>
  API_MODE === "legacy" ? `/${resource}.php` : `/${resource}`;
```

경로 치환(`ep("invoices")`→`"/invoices"` 등): `ep("invoices")`, `ep("companies")`, `ep("items")`, `ep("settings")` 호출 전부를 대응 리터럴로. 예:

- `api.get(ep("invoices"), {` → `api.get("/invoices", {`
- `` `${ep("invoices")}/${id}` `` → `` `/invoices/${id}` ``
- `` `${ep("settings")}/issuer` `` → `` `/settings/issuer` ``
- `` `${ep("settings")}/app` `` → `` `/settings/app` ``

- [ ] **Step 2: invoice.duplicate legacy fallback 제거 (`:97-111`)**

```ts
  duplicate: async (id: number): Promise<SingleResponse<Invoice>> => {
    const response = await api.post(`/invoices/${id}/duplicate`);
    return response.data;
  },
```

- [ ] **Step 3: invoice.export modern-only 가드 제거 (`:113-124`)**

`if (API_MODE !== "modern") throw ...` 2줄(`:117-118`)을 제거. 결과:

```ts
  export: async (
    format: "csv" | "xlsx",
    filters?: InvoiceFilters,
  ): Promise<Blob> => {
    const response = await api.get(`/invoices/export`, {
      params: { format, ...filters },
      responseType: "blob",
    });
    return response.data;
  },
```

- [ ] **Step 4: company.update / item.update의 delete+create fallback 제거**

`_realCompanySuggestionsAPI.update`(`:147-174`)를 modern 본문만 남긴다:

```ts
  update: async (
    id: number,
    company: Partial<Company>,
  ): Promise<SingleResponse<Company>> => {
    const response = await api.put(`/companies/${id}`, company);
    return response.data;
  },
```

`_realItemSuggestionsAPI.update`(`:205-232`)도 동일 패턴:

```ts
  update: async (
    id: number,
    item: Partial<Item>,
  ): Promise<SingleResponse<Item>> => {
    const response = await api.put(`/items/${id}`, item);
    return response.data;
  },
```

- [ ] **Step 5: settings의 localStorage fallback 제거**

`getIssuer`(`:245-250`) — endpoint 삼항 제거:

```ts
  getIssuer: async (): Promise<SingleResponse<Issuer>> => {
    const response = await api.get(`/settings/issuer`);
    return response.data;
  },
```

`saveIssuer`(`:252-259`) — modern 본문만:

```ts
  saveIssuer: async (issuer: Issuer): Promise<SingleResponse<Issuer>> => {
    const response = await api.put(`/settings/issuer`, issuer);
    return response.data;
  },
```

`getAppSettings`(`:261-290`) — `if (API_MODE === "modern") {` 가드와 `// legacy: localStorage fallback`(`:280-289`) 블록 제거, modern 본문을 함수 본문으로 승격. `updateAppSettings`(`:292-313`)도 동일하게 modern 본문만 남기고 localStorage fallback(`:308-312`) 제거.
그 결과 `APP_SETTINGS_KEY` 상수(`:242`)가 미사용이 되면 함께 제거한다(YOUR-change orphan cleanup).

- [ ] **Step 6: VITE_API_MODE 잔여 참조 0 확인 + 게이트 green**

```bash
cd apps/invoice-ocr/frontend
grep -rn "API_MODE\|\.php\|legacy fallback\|Legacy API\|localStorage fallback" src/services/api.ts
```

Expected: 빈 출력.

```bash
npm run lint && npm run build && npm run test
```

Expected: lint·tsc·vitest 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
cd apps/invoice-ocr/frontend
git add src/services/api.ts
git commit -m "$(cat <<'EOF'
refactor(frontend): api.ts legacy 분기 제거, modern 경로 단일화

VITE_API_MODE/ep()/legacy fallback 제거. .php 경로는 FastAPI에 부재해
이미 비작동 → 실배포 무영향.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

### Task 4: mock `ListResponse`를 nested `pagination`으로 단일화

**Files:**

- Modify: `apps/invoice-ocr/frontend/src/mocks/api.ts`

**Interfaces:**

- Consumes: `ListResponse<T>` (`@/types/api`) — 이 시점엔 아직 flat 호환 필드 존재(green 유지)
- Produces: mock `getList`들이 `{ data, pagination: {page, limit, total, totalPages} }` 형태 반환 → 실 백엔드(구조화 envelope)와 동일 형태

> mock은 `VITE_USE_MOCK=true`일 때만 런타임에 쓰이며 현재 비활성이다. 이 변경의 목적은 Task 5에서 flat 호환 필드를 제거해도 mock이 tsc green을 유지하게 하는 것(형태 정합).

- [ ] **Step 1: invoices getList 반환 nested화 (`:87`)**

OLD:

```ts
return { data: paged, total, page, limit };
```

NEW:

```ts
return {
  data: paged,
  pagination: { page, limit, total, totalPages: Math.ceil(total / limit) },
};
```

- [ ] **Step 2: companies getList 반환 nested화 (`:166`)**

OLD:

```ts
return { data: list, total: list.length, page: 1, limit: list.length };
```

NEW:

```ts
return {
  data: list,
  pagination: {
    page: 1,
    limit: list.length,
    total: list.length,
    totalPages: 1,
  },
};
```

- [ ] **Step 3: items getList 반환 nested화 (`:228`)**

OLD:

```ts
return { data: list, total: list.length, page: 1, limit: list.length };
```

NEW:

```ts
return {
  data: list,
  pagination: {
    page: 1,
    limit: list.length,
    total: list.length,
    totalPages: 1,
  },
};
```

- [ ] **Step 4: salespeople getList 반환 nested화 (`:308-312` 부근)**

`Read`로 `:300-315` 블록을 확인해 정확한 반환 객체를 잡는다. flat `{ ..., total: salespeople.length, page: 1, limit: salespeople.length }`를 nested `pagination: { page: 1, limit: salespeople.length, total: salespeople.length, totalPages: 1 }`로 바꾼다.

- [ ] **Step 5: mock에 flat 필드 잔여 0 확인 + 게이트 green**

```bash
cd apps/invoice-ocr/frontend
grep -nE "return \{[^}]*\btotal:|\bpage: 1, limit:" src/mocks/api.ts
```

Expected: 빈 출력(모든 list 반환이 nested). (invoices.ts의 품목 `total:`은 무관 — `src/mocks/api.ts`만 검사.)

```bash
npm run lint && npm run build && npm run test
```

Expected: 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
cd apps/invoice-ocr/frontend
git add src/mocks/api.ts
git commit -m "$(cat <<'EOF'
refactor(frontend): mock 목록 응답을 nested pagination으로 단일화

mock getList 반환을 실 백엔드 구조화 envelope와 동일한
{ data, pagination } 형태로 통일(flat total/page/limit 제거 준비).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

### Task 5: `types/api.ts` flat 호환 필드 제거 + hook fallback + 테스트 정합

**Files:**

- Modify: `apps/invoice-ocr/frontend/src/types/api.ts`
- Modify: `apps/invoice-ocr/frontend/src/hooks/use-invoices.ts`
- Modify: `apps/invoice-ocr/frontend/src/hooks/use-invoices.test.ts`

**Interfaces:**

- Consumes: nested `pagination`만 (실 백엔드·mock 모두 nested로 정렬 완료)
- Produces: `ListResponse<T>` = `{ success?, data, pagination? }` (flat `total?`/`page?`/`limit?` 제거). `useInvoices().total` 동작·`use-invoices.test.ts` 단언값 불변.

> 이것이 Phase 0의 유일한 "동작 인접" seam이다. `use-invoices.ts:34-37`의 `res.total` fallback은 flat mock 전용이었다. 실 백엔드는 항상 `res.pagination.total`을 준다. mock이 Task 4에서 nested로 옮겨졌으므로 fallback을 제거해도 `total` 계산 결과는 동일하다.

- [ ] **Step 1: `types/api.ts`에서 flat 호환 필드 제거 (`:8-17`)**

OLD:

```ts
export interface ListResponse<T> {
  success?: boolean;
  data: T[];
  /** modern 구조화 envelope(FastAPI, 1B B안 확정) — total은 여기 중첩된다. */
  pagination?: Pagination;
  /** legacy 평평 형태(mock 픽스처) 호환 필드 — 런타임 mock 비활성. */
  total?: number;
  page?: number;
  limit?: number;
}
```

NEW:

```ts
export interface ListResponse<T> {
  success?: boolean;
  data: T[];
  /** 구조화 envelope — total은 pagination에 중첩된다. */
  pagination?: Pagination;
}
```

- [ ] **Step 2: `use-invoices.ts` fallback 제거 (`:34-38`)**

OLD:

```ts
// modern 구조화는 total을 pagination에 중첩(1B B안). mock 평평 형태는 최상위 total.
setTotal(
  res.pagination?.total ?? (typeof res.total === "number" ? res.total : 0),
);
```

NEW:

```ts
setTotal(res.pagination?.total ?? 0);
```

- [ ] **Step 3: 테스트 헬퍼 `createListResponse` nested화 (`use-invoices.test.ts:32-37`)**

OLD:

```ts
function createListResponse(
  data: Invoice[],
  total?: number,
): ListResponse<Invoice> {
  return { data, total: total ?? data.length, page: 1, limit: 20 };
}
```

NEW:

```ts
function createListResponse(
  data: Invoice[],
  total?: number,
): ListResponse<Invoice> {
  return {
    data,
    pagination: {
      page: 1,
      limit: 20,
      total: total ?? data.length,
      totalPages: 1,
    },
  };
}
```

- [ ] **Step 4: 게이트 green — 단언값 보존 확인**

```bash
cd apps/invoice-ocr/frontend
npm run lint && npm run build && npm run test
```

Expected: 전부 PASS. 특히 `use-invoices.test.ts`의 `expect(result.current.total).toBe(1)`(목록 조회 테스트)이 여전히 PASS — nested 경로로 동일 값 산출. flat 필드 제거로 인한 tsc 에러 0.

- [ ] **Step 5: flat 필드 잔여 0 확인**

```bash
cd apps/invoice-ocr/frontend
grep -rn "res\.total\|\.total ?? \|total?: number\|page?: number\|limit?: number" src/types/api.ts src/hooks/use-invoices.ts
```

Expected: 빈 출력.

- [ ] **Step 6: 커밋**

```bash
cd apps/invoice-ocr/frontend
git add src/types/api.ts src/hooks/use-invoices.ts src/hooks/use-invoices.test.ts
git commit -m "$(cat <<'EOF'
refactor(frontend): ListResponse flat 호환 필드 제거(nested pagination 단일화)

legacy mock 전용 flat total/page/limit 제거. useInvoices의 flat fallback도
제거(실 백엔드·mock 모두 pagination 중첩). total 동작·테스트 단언 불변.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

---

## PR3 — api-spec 정리 + 명문화 (Task 6–7)

### Task 6: `api-spec.json` 원본 결부 6개 사이트 중립화

**Files:**

- Modify: `.claude/ai-context/api-spec.json` (라인 6, 88, 95, 328, 383, 576)

**Interfaces:**

- Consumes: 없음 (description 문자열 전용)
- Produces: 없음 (스키마 구조·enum·필드 불변)

- [ ] **Step 1: 확정 치환 (description 문자열만)**

| 라인   | OLD 조각                                                           | NEW 조각                                                                              |
| ------ | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------- | ------------------ | ---------------------------------------------------------- |
| `:6`   | `백엔드(FastAPI). PHP SJMJ-Web을 동형 포팅한 REST/JSON API.`       | `백엔드(FastAPI). REST/JSON API.`                                                     |
| `:88`  | ` 검증 실패 details 는 {필드: 메시지} 맵 (PHP 골든 메시지 동치).`  | ` 검증 실패 details 는 {필드: 메시지} 문자열 맵(contract 테스트로 고정).`             |
| `:95`  | `free-form (Pydantic 모델 아님 — PHP 동형 골든 메시지 보존 목적).` | `free-form (Pydantic 모델 아님 — 검증 메시지·details 형태를 contract 테스트로 고정).` |
| `:328` | `"description": "표준 에러 응답. PHP Response::error 동형.",`      | `"description": "표준 에러 응답.",`                                                   |
| `:383` | `누락 시 0으로 저장하므로, 생략하면 합계가 0이 된다(PHP 동형).`    | `누락 시 0으로 저장하므로, 생략하면 합계가 0이 된다.`                                 |
| `:576` | `라우터 \_is_int(PHP is_int                                        |                                                                                       | is_numeric 동형).` | `라우터 _is_int(정수 또는 정수 문자열만 허용, bool 제외).` |

- [ ] **Step 2: 잔여 토큰 0 확인 + JSON 유효성**

```bash
cd /Users/gangsub/projects/sjmj-ai
grep -nE "PHP|동형|동치|골든|Response::|SJMJ-Web" .claude/ai-context/api-spec.json
python3 -c "import json; json.load(open('.claude/ai-context/api-spec.json')); print('valid json')"
```

Expected: grep 빈 출력, `valid json` 출력.

- [ ] **Step 3: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add .claude/ai-context/api-spec.json
git commit -m "$(cat <<'EOF'
docs(api-spec): 원본 결부 출처 6곳 중립화

PHP/동형/골든 언급을 contract 테스트 기준 서술로 교체(스키마 구조 불변).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

### Task 7: 목표 컨벤션·점진 규칙·불변식·전환 체크리스트 명문화

**Files:**

- Modify: `apps/invoice-ocr/backend/AGENTS.md` (컨벤션/함정 섹션에 추가)
- Modify: `.claude/rules/api-conventions.md` (스키마 검증 섹션 근처에 추가)
- Modify: `AGENTS.md` (루트, 백엔드 아키텍처 섹션 말미에 추가)

**Interfaces:**

- Consumes: spec `docs/superpowers/specs/2026-06-30-fastapi-convention-modernization-design.md` (§목표 컨벤션·§외부 계약 불변식·§점진 전환)
- Produces: 미래 에이전트가 이 문서만 읽고 다음 슬라이스를 목표 컨벤션으로 전환할 수 있는 자족 지침 + 전환 현황 체크리스트(리스크 §일관성 드리프트 완화)

> 이전 세션에서 AGENTS.md·api-conventions.md의 PHP 출처 언급은 이미 제거됨(commit 952946c). 이 태스크는 거기에 "목표 컨벤션 + 점진 규칙 + 불변식 + 체크리스트"를 **새로 더한다**.

- [ ] **Step 1: 루트 `AGENTS.md` — "백엔드 아키텍처" 섹션 말미에 목표 컨벤션 단락 추가**

`신규 도메인(슬라이스)을 추가할 때는 ...` 문단 바로 뒤에 아래 블록을 삽입:

```markdown
**목표 컨벤션(점진 전환 중).** 슬라이스를 신규 추가·수정할 때 그 범위를 실용적 FastAPI 관용구로 끌어올린다(빅뱅 아님). 유지: `sync def`+threadpool·SQLAlchemy Core raw `text()`·응답 envelope shape. 전환: free-form `dict = Body(...)` → Pydantic request 모델, fluent `Validator` → Pydantic 검증, 검증 메시지 문자열 자유화. **외부 계약 불변식**(아래)을 지키는 한 내부 구현은 자유다. 근거·전환 절차는 `docs/superpowers/specs/2026-06-30-fastapi-convention-modernization-design.md`.

**외부 계약 불변식(절대 보존):** 성공 envelope `{success, data, pagination?}` · 에러 envelope `{success, error: {code, message, details?}}` · 에러 코드 체계(`VALIDATION_ERROR`/`NOT_FOUND`/`DUPLICATE_NAME`/`CONFLICT`/`SERVER_ERROR`) · 검증 실패 HTTP status **400** · `details` 형태 `{필드: 메시지}` 문자열 맵. Pydantic 전환 슬라이스는 `RequestValidationError` 핸들러로 422를 이 400 envelope로 변환해야 한다(첫 Pydantic 슬라이스가 선결로 도입).
```

- [ ] **Step 2: `backend/AGENTS.md` — "컨벤션/함정" 섹션에 전환 규칙 + 체크리스트 추가**

기존 마지막 불릿(`**신규 slice 추가 시** ...`) 뒤에 아래를 추가:

```markdown
- **점진 현대화 트리거.** 슬라이스에 신규 기능·수정이 들어오면 그 슬라이스의 router+service+repository+tests를 목표 컨벤션(루트 `AGENTS.md` 참조)으로 함께 끌어올린다. 손대지 않는 슬라이스는 그대로 둔다. 응답 envelope shape가 불변이라 프론트 동시 수정은 대개 불필요.
- **Pydantic 전환 선결 인프라.** 첫 Pydantic 슬라이스는 `app/core/errors.py`에 `RequestValidationError` 핸들러를 추가해 Pydantic 검증 실패(기본 422)를 400 `{success, error:{code:"VALIDATION_ERROR", message, details:{필드:메시지}}}`로 변환하고, 그 동작을 고정하는 contract 테스트를 함께 둔다. 이후 슬라이스는 이 핸들러를 공유한다.

### 슬라이스 전환 현황

| slice         | 검증 방식 | 비고   |
| ------------- | --------- | ------ |
| invoices      | Validator | 미전환 |
| companies     | Validator | 미전환 |
| items         | Validator | 미전환 |
| settings      | Validator | 미전환 |
| salespeople   | Validator | 미전환 |
| sales_records | Validator | 미전환 |
| ocr           | Validator | 미전환 |

> 슬라이스를 Pydantic으로 전환하면 이 표의 "검증 방식"을 `Pydantic`으로 갱신한다.
```

- [ ] **Step 3: `.claude/rules/api-conventions.md` — "스키마 검증" 섹션에 전환 주석 추가**

`## 스키마 검증 (라우터 특화)` 섹션의 첫 불릿(현재 `dict = Body(...)` free-form 설명) 바로 뒤에 아래를 추가:

```markdown
- **점진 전환 중.** 위 free-form + Validator는 현재(미전환) 슬라이스의 방식이다. 슬라이스를 신규/수정할 때 Pydantic request 모델 + `field_validator`/`model_validator`로 전환하되, **검증 실패는 여전히 400 `VALIDATION_ERROR` + `{필드: 메시지}` details로 응답**해야 한다(`RequestValidationError` 핸들러가 422→400 변환). envelope shape·에러 코드·400 status·details 형태는 외부 계약 불변식이다.
```

- [ ] **Step 4: 명문화 정합성 확인**

```bash
cd /Users/gangsub/projects/sjmj-ai
grep -n "외부 계약 불변식\|목표 컨벤션\|RequestValidationError\|전환 현황" AGENTS.md apps/invoice-ocr/backend/AGENTS.md .claude/rules/api-conventions.md
npx prettier --check AGENTS.md apps/invoice-ocr/backend/AGENTS.md .claude/rules/api-conventions.md 2>/dev/null || echo "prettier가 재포맷 시 git add 후 재커밋"
```

Expected: 세 파일에 추가 블록이 보임. prettier 불일치 시 `npx prettier --write`로 정리.

- [ ] **Step 5: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add AGENTS.md apps/invoice-ocr/backend/AGENTS.md .claude/rules/api-conventions.md
git commit -m "$(cat <<'EOF'
docs(context): 목표 FastAPI 컨벤션·점진 전환 규칙·외부 계약 불변식 명문화

미래 에이전트가 슬라이스를 목표 컨벤션으로 전환할 자족 지침 + 전환 현황
체크리스트 추가. Pydantic 전환의 RequestValidationError 핸들러 선결 명시.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EKBcfkX9ezXXckvABEETMQ
EOF
)"
```

---

## 완료 기준 (Phase 0 DoD)

- [ ] `grep -rniE "php|동형|동치|골든|golden|sjmj-web|response::" apps/invoice-ocr/backend/app apps/invoice-ocr/backend/tests .claude/ai-context/api-spec.json | grep -v "invoices.py:129"` → 빈 출력(보존 예외만).
- [ ] `grep -rn "API_MODE\|VITE_API_MODE\|\.php" apps/invoice-ocr/frontend/src` → 빈 출력.
- [ ] backend `uv run ruff check . && uv run pytest -q` 전부 PASS(동작 불변).
- [ ] frontend `npm run lint && npm run build && npm run test` 전부 PASS(`useInvoices().total` 단언 포함).
- [ ] 루트·backend `AGENTS.md` + `api-conventions.md`만 읽고 다음 슬라이스를 목표 컨벤션으로 전환할 수 있다(불변식·트리거·선결 핸들러·전환 현황 표 자족).
- [ ] 7개 커밋이 PR1(T1-2)/PR2(T3-5)/PR3(T6-7) 단위로 분리됨.
