# Phase 1B — 백엔드 API 포팅 spec (FastAPI, 계약 1:1)

- 작성일: 2026-06-28
- 성격: 로드맵 [`2026-06-27-sjmj-ai-phased-roadmap-design.md`](./2026-06-27-sjmj-ai-phased-roadmap-design.md) §4 **1B의 자체 spec**. 다음 단계는 이 spec → plan(writing-plans) → TDD 구현.
- 근거: 계약 인벤토리 [`2026-06-28-phase1b-contract-inventory.md`](./2026-06-28-phase1b-contract-inventory.md)(30라우트·충돌 C1~C10·골든 228 분류). 모든 수치는 2026-06-28 `~/projects/SJMJ-Web` 실측.
- 확정 결정(사용자 승인): **envelope = B(modern 구조화)**, **vertical slice = invoices**.

---

## 1. 목표 & 범위

원본 SJMJ-Web의 **정본 Front Controller(`index.php`) 30라우트**를 FastAPI로 1:1 포팅한다. 합격 = **계약 동치**(원본 modern 응답과 동일한 envelope 형태·필드값·상태코드·검증). 레거시 절차적 `api/*.php`(평평·무테스트)는 **포팅하지 않는다**(은퇴 대상).

**범위 포함**: 6리소스 30라우트 + 비-JSON 2표면(stamp 멀티파트 업로드, invoices export CSV) + 골든 228의 계약 동치 이식 + ML 이음새 env 통합.

**범위 제외(다른 단계)**: 프론트 수정(→ 1C 핸드오프 §7), macmini launchd·배포(→ 1D), 실제 ML 코드·worker(→ Phase 2). 운영 DB 적재는 1A에서 정본화됨(스키마), 런타임 연결은 1D.

---

## 2. 확정된 설계 결정

| # | 결정 | 값 | 근거 |
|---|---|---|---|
| D1 | envelope 정본 | **구조화** `{success,data,pagination}` (`core/Response.php` 동형) | 골든=DoD 최대 충실 + 전 기능 복원 (인벤토리 §중심결정 B) |
| D2 | vertical slice 1번 | **invoices** | envelope 충돌이 런타임 깨짐으로 발현되는 유일 리소스 + export/duplicate/조인/CASCADE 동시 반증 |
| D3 | DB 접근 | **SQLAlchemy 2.0 Core(sync) + `text()` 원시 SQL** + PyMySQL 드라이버 | PDO 리포지토리의 upsert(ON DUPLICATE KEY)·서브쿼리·LIKE를 1:1 이식, SQL 가시성 유지(KISS). ORM 매핑 회피 |
| D4 | 동기/비동기 | **sync** def 엔드포인트 | 원본 PDO도 sync, 규모상 async 불필요(KISS) |
| D5 | 설정 | **pydantic-settings BaseSettings** | env 경계 검증 + AppConfigTest 골든(빈 PASS 존중) 재현 |
| D6 | 계층 | `router → service → repository`(PHP 4-layer 대응) | 골든이 계층별로 분리돼 있어 동형 유지 시 이식 비용 최소 |
| D7 | 검증 | **pydantic 모델 + 명시 Validator 동치** | Validator 15 골든을 pydantic 규칙으로 값 이식, 에러는 D1 envelope의 `VALIDATION_ERROR details` 형태 |
| D8 | DELETE 경로 | **path-param `/{id}` 정본**(RESTful·골든 일치) | 프론트 `?id=` 쿼리는 1C 핸드오프로 수정 |

---

## 3. 타깃 아키텍처

### 디렉터리 (현 `apps/invoice-ocr/backend/app/` 확장)

```
app/
  main.py            # FastAPI 앱 + 라우터 등록 + 예외 핸들러(health/static는 SP0 유지)
  config.py          # pydantic-settings: SJMJ_PORT·SJMJ_STATIC_DIR(기존) + SJMJ_DB_*·SJMJ_DATA_DIR·SJMJ_DB_BACKUP(신규)
  db.py              # SQLAlchemy Engine 팩토리(+테스트용 createForTest/setTestInstance 대응)
  core/
    envelope.py      # Response 동형: list/single/created/deleted/error → JSONResponse(success/data/pagination/error.code)
    errors.py        # AppError(VALIDATION_ERROR/NOT_FOUND/DUPLICATE_NAME/SERVER_ERROR) + FastAPI exception_handler
    validators.py    # required/maxLength/dateFormat/businessNumber/numeric/nonEmptyArray 동치
  routers/           # 리소스별 라우터 6: invoices·companies·items·settings·salespeople·sales_records
  services/          # 비즈니스 로직 6(+export) — PHP Service 동형
  repositories/      # PDO 리포 동형 6 — text() 원시 SQL
  models/            # pydantic 요청/응답 모델(리소스별)
tests/
  conftest.py        # 실 MySQL(sjmj_test) fixture + tx 롤백 격리(DatabaseTestCase 대응), TestData 팩토리 이식
  contract/          # Controller 골든 동치(httpx TestClient) — envelope·상태·error.code
  unit/              # Service·Validator 골든(mock repo)
  integration/       # Repository 골든(실 DB)
```

### envelope 모듈 명세 (`core/envelope.py`) — D1 정본

```
list(data, pagination)  → 200 {success:true, data:[...], pagination:{page,limit,total,totalPages}}
single(data)            → 200 {success:true, data:{...}}
created(data)           → 201 {success:true, data:{...}}
deleted(message)        → 200 {success:true, data:null, message}
error(status,code,msg,details?) → {success:false, error:{code,message,details?}}
```
- 미처리 예외 → 500 `{success:false, error:{code:'SERVER_ERROR', message}}` (Middleware::handleErrors 동치).
- export만 envelope 밖 `StreamingResponse(text/csv)` — JSON 래핑 금지(C8).

### 계층 매핑 (원본 → FastAPI)

| PHP | FastAPI | 비고 |
|---|---|---|
| `core/Router` + `index.php` 등록 | `routers/*` + `main.include_router` | 라우트 순서: `/invoices/export`를 `/invoices/{id}`보다 먼저(C: 경로 충돌 방지) |
| `core/Request` | FastAPI `Request`/pydantic 모델 | query/body 파싱 |
| `core/Response` + `HttpResponseException` | `core/envelope` + `core/errors` | throw→catch 대신 FastAPI exception_handler |
| `core/Validator` | `core/validators` + pydantic | 값 동치 |
| `controllers/*` | `routers/*`(얇게) + `services/*` | 컨트롤러 검증 로직은 라우터/모델로 |
| `services/*`, `ExportService` | `services/*` | 비즈니스 로직 동형 |
| `repositories/*`(PDO) | `repositories/*`(SQLAlchemy Core text()) | upsert·서브쿼리·LIKE 1:1 |
| `config/app.php`,`database.php` | `config.py` | env override + 빈 PASS 존중(AppConfig 골든) |

---

## 4. DoD — 계약 동치 (골든 비교, 선택 아님)

원본 modern 응답과의 **계약 동치**를 골든으로 강제한다. 바이트매치가 아니라 4축 동치 + envelope 스키마 준수:

1. **값 동치**: Service/Repository 반환값·계산(totals, tel_fax 조립, snapshot_name, incrementUsage 누적, pagination 값, CASCADE/FK RESTRICT).
2. **상태·에러코드 동치**: 200/201/400/404/409/500 + `error.code`(VALIDATION_ERROR/NOT_FOUND/DUPLICATE_NAME/SERVER_ERROR).
3. **검증규칙 동치**: Validator 15 + 도메인 규칙(name trim·제어문자·중복활성, 음수 qty, year_month, business# 패턴) 동일 입력→동일 합/거부.
4. **envelope 스키마 준수**: D1 구조화 형태.

**테스트 이식 3갈래(인벤토리 §4)**: 직접재사용 ≈82(Repository 65+Validator 15+Export 2) · 값재사용 ≈121(Controller 67+Service 54) · 재작성 ≈25. **커버리지 80%↑, TDD(RED→GREEN→REFACTOR).** Repository는 실 MySQL `sjmj_test` + tx 롤백 격리(conftest fixture), `schema-test.sql`·`TestData` 팩토리를 pytest 골든 데이터로 이식.

---

## 5. 리소스별 계약 결정 (충돌 해소 위치)

원칙: **modern 동작 1:1 보존(golden 충실)**. 프론트 divergence는 §7 1C 핸드오프로 미룬다. 아래는 spec이 취하는 위치.

| 충돌 | 위치(1B 백엔드) | 결정 |
|---|---|---|
| C2 total 중첩(invoices) | 백엔드 = 구조화 유지 | `pagination.total` 정본. 프론트는 1C에서 `res.pagination.total` 읽도록 1줄 수정 |
| C: DELETE `?id=`(comp/item) | path-param `/{id}` 정본(D8) | 프론트 delete URL을 1C에서 path-param으로 |
| C: invoices sort_by 키 | 백엔드 화이트리스트 `issue_date/grand_total/recipient/created_at` 유지 | 프론트 SORT_MAP을 1C에서 백엔드 키로 매핑 |
| items POST 중복 item_name | INSERT 201 보존 + **UNIQUE 위반 시 409 DUPLICATE_NAME**(modern의 500 위험은 버그 → graceful) | golden store 201 유지, 중복만 409 |
| items 카테고리 어휘 | 백엔드 VARCHAR 자유(필터 정확일치 보존) | 영문↔한글 정합은 1C/데이터 과제(1B 비차단) |
| settings default_vat_rate | 백엔드 `'0.1'` string 보존(golden) | number 변환은 1C 프론트 |
| settings stamp(C1) | issuer upsert에 `stamp_image_url` 포함(modern 보존) + 멀티파트 엔드포인트 동형 제공 | base64 인라인 경로가 실사용 — 1C에서 정합 |
| salespeople update name 필수 | modern 보존(name required) | swapOrder의 부분수정은 1C(프론트가 name 동봉 or 백엔드 PATCH는 후속) |
| 가짜 pagination(comp/item/sp) | modern 동작 박제(page1/limit9999/totalPages1) | golden ItemServiceTest 리터럴 단언 유지 |
| usage_count 부수효과 | modern 보존(create만 증가, update 불변, duplicate=create 경로) | |
| updated_at 유령 필드 | modern SELECT 컬럼셋 보존(없는 곳은 미포함) | |
| 숫자 직렬화 | id/quantity/sort_order int, is_active 0\|1 명시 | pydantic 응답 모델로 보장 |

---

## 6. ML 이음새 (env 통합)

`config.py`를 pydantic-settings로 확장(인벤토리 §7):

| env | 용도 | 검증 |
|---|---|---|
| `SJMJ_DB_HOST/PORT/NAME/USER/PASS` (신규) | 런타임 MySQL 연결 | BaseSettings, 빈 PASS 존중(AppConfig 골든) |
| `SJMJ_DATA_DIR` (신규) | 데이터 루트(ocr_poc 동형) | 부재 시 RuntimeError |
| `SJMJ_DB_BACKUP` (신규) | 정본 DB 덤프(.sql) 경로 | 부재 시 RuntimeError |
| `SJMJ_PORT`·`SJMJ_STATIC_DIR` | 기존 | 유지 |

규약: 경계에서만 env 해석(절대경로 하드코딩 금지), `ocr_poc/config.py`와 동일. `SJMJ_DB_BACKUP`(백업 경로) ≠ `SJMJ_DB_*`(라이브 커넥션).

---

## 7. 프론트 reconciliation 핸드오프 → 1C

1B는 백엔드만 바꾸고, 무변경 프론트가 modern 백엔드에 붙도록 1C가 처리할 **국소 수정 목록**을 산출한다(다수가 버그 수정):

1. `.env`/`.env.production`: `VITE_API_MODE=modern`, `VITE_API_URL`을 macmini backend(:8400)로.
2. `use-invoices.ts:34`: `res.total` → `res.pagination.total`(+ `types/api.ts` `ListResponse`를 구조화로).
3. companies/items DELETE: `?id=` → path-param `/{id}`(`api.ts`).
4. invoices `SORT_MAP`: `date/amount/company` → `issue_date/grand_total/recipient`.
5. settings `default_vat_rate`: `'0.1'` string ↔ number 변환 + `AppSettings`에 `default_unit` 추가.
6. items 카테고리 어휘 정합(영문↔한글) — 데이터/타입 점검.
7. salespeople `swapOrder`: PUT에 `name` 동봉(또는 백엔드 PATCH 후속).
8. legacy 폴백 제거(company/item update의 delete+create, duplicate 클라 우회, app settings localStorage) — modern 경로 사용으로 자연 비활성.

---

## 8. 진행 — vertical slice → 수평 팬아웃

**0단계(slice): invoices를 end-to-end 관통.** routers/services/repositories/models + contract/unit/integration 골든까지 1리소스 완주. 통과 기준 = invoices 7라우트(+export CSV +duplicate) 계약 동치 + 실 DB 리포 골든 통과 + 무변경 프론트(modern 플립 + §7-2 수정)로 목록·작성·수정·PDF가 동작. 이 한 줄기가 "구조화 envelope/기능 게이트/비-JSON 응답/실 DB tx" 4대 가정을 반증한다.

**1단계(팬아웃): 나머지 5리소스(companies·items·settings·salespeople·sales-records) 병렬 포팅.** slice가 확정한 envelope·검증·리포 패턴을 재사용. 리소스 독립이라 dynamic workflow 팬아웃(리소스당 1에이전트가 포팅+계약 골든)에 적합, 산출은 standard pipeline PR→리뷰→merge 게이트.

**도구(로드맵 §2)**: workflow(병렬 포팅+골든) → standard pipeline(머지 게이트) 하이브리드. 단 slice(invoices)는 가정 반증이 목적이라 **직접 TDD로 신중히** 관통한 뒤 팬아웃을 자동화한다.

---

## 9. 테스트 전략

- **프레임워크**: pytest + httpx(`TestClient`) + SQLAlchemy. 현 `pyproject.toml`에 `sqlalchemy`·`pymysql`·`pydantic-settings`·`pytest-cov` 추가.
- **contract(Controller 67 동치)**: `TestClient`로 라우트 호출 → `response.json()`의 envelope·상태·`error.code`·`data` 단언. mock service(또는 DI override)로 PHP `createMock` 대응.
- **unit(Service 54 + Validator 15 + Export 2)**: mock repository로 비즈니스 규칙·검증·sanitizeCsvField 값 동치.
- **integration(Repository 65)**: 실 `sjmj_test` MySQL, `conftest`가 `schema-test.sql` 1회 적재 + 테스트마다 tx 롤백 격리. upsert·검색·정렬·CASCADE·FK RESTRICT·incrementUsage 값 동치.
- **재작성(25)**: Response/Router/HttpException/AppConfig → FastAPI/pydantic 기본 + 404/405 스모크 + BaseSettings 테스트.

---

## 10. 리스크 / 미해결

- **계약 드리프트**: 미세 불일치 시 무변경 프론트 깨짐 → 골든 계약 동치를 DoD로 강제(§4). slice(invoices)에서 조기 반증.
- **실 DB 골든 하니스**: Repository 65의 충실 이식은 `sjmj_test` MySQL + tx 롤백 fixture 구축이 전제(난이도 중). slice에서 먼저 세운다.
- **stamp 경로 모호성(C1)**: 멀티파트(고아) vs base64 인라인(실사용) 중 정본 — 1B는 둘 다 동형 제공하되, 정합은 1C에서. (미해결: 멀티파트로 통일할지 인라인 유지할지)
- **salespeople PATCH 시맨틱**: name-필수 vs 부분수정 — 1B는 modern(name 필수) 보존, PATCH 도입은 후속 결정.
- **category 어휘 정본**: 영문/한글 합의는 1B 비차단이나 1C 전 확정 필요.

> **1B 종료 기준(DoD)**: 30라우트 + 비-JSON 2표면이 원본 modern과 계약 동치(값·상태·에러코드·검증·envelope)이고, 골든 228이 계약 동치로 이식돼 통과(커버리지 80%↑), §7 1C 핸드오프 목록이 산출된다.
