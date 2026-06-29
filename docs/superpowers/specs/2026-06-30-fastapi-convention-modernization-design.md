# 독자 노선 전환 + 실용적 FastAPI 현대화 (점진)

`invoice-ocr`는 원본을 동형 포팅한 단계를 졸업하고 독자 노선으로 전환한다.
원본 결부(출처 주석·동치 계약 프레이밍)와 legacy API 분기를 걷어내고, 백엔드를 실용적 FastAPI 관용구로 점진 현대화한다.
빅뱅 마이그레이션이 아니라, 파일을 만질 때 영향 범위만 목표 컨벤션으로 끌어올리는 boy-scout 방식이다.

## 배경

백엔드와 프론트엔드는 원본 프로젝트를 충실히 동형 포팅했고, 그 충실성이 코드 전반에 남아 있다.

- backend: 모든 router/service/repository/core 파일에 "…Controller.php 동형" 류 docstring·주석 57곳. 검증 메시지 문자열·응답 포맷·부수효과를 문자 단위로 고정한 골든 테스트 29파일 900+줄.
- backend의 "비관용" 패턴(33개 엔드포인트 `sync def`, 12개 write의 free-form `dict = Body(...)` + fluent Validator, 수동 envelope 래핑, ORM 없는 raw SQL)은 전부 원본 검증 메시지·응답 포맷을 보존하려는 의도였다.
- frontend: `src/services/api.ts` 한 파일에 legacy/modern 분기 9곳(~121줄). `VITE_API_MODE`는 이미 `modern` 고정이라 legacy 경로는 사실상 죽은 코드다.
- 설정·CI·ML 계층은 이미 원본/legacy 흔적이 없다.

원본 레포가 사장되면서 "동치 계약 보존"이라는 제약의 근거가 사라졌다.
이제 외부 계약(프론트가 의존하는 응답 형태)만 지키면, 내부 구현은 자유롭게 현대화할 수 있다.

## 목표와 비목표

**목표**

- 원본 결부 주석·docstring·문서·명세 언급 제거 (독자 노선 baseline 확보)
- frontend legacy 분기 제거 (modern 단일화)
- 백엔드의 진짜 비관용 패턴을 실용적 FastAPI 관용구로 **점진** 전환할 기준과 규칙을 명문화
- 미래의 모든 수정이 수렴할 목표 컨벤션(target state) 정의

**비목표 (이번 범위 아님)**

- async I/O 전환(비동기 드라이버 교체) — `sync def` + threadpool은 블로킹 DB에 정당한 패턴
- SQLAlchemy ORM 도입 — raw SQL Core + 정렬 화이트리스트도 정당한 선택
- 빅뱅 일괄 재작성 — 슬라이스 단위 점진
- ML 모듈 변경 — 원본/legacy와 무관

## 목표 컨벤션 (target state)

"실용적 현대화" — 비용 대비 효과가 분명한 것만 바꾸고, 정당한 패턴은 유지한다.

### 유지 (FastAPI에서 정당한 패턴)

- **`sync def` 엔드포인트 + threadpool** — 동기 SQLAlchemy를 async 핸들러에서 호출하면 이벤트 루프를 막는다. 블로킹 DB엔 `sync def`가 권장.
- **SQLAlchemy Core + raw `text()`** — 정렬 컬럼 화이트리스트(`_ALLOWED_SORT_*`), named placeholder 파라미터 바인딩 유지.
- **일관 응답 envelope shape** — 외부 계약이므로 보존(아래 §외부 계약 불변식).

### 전환 (비관용 → 관용)

- **free-form `dict = Body(...)` → Pydantic request 모델.** 필드 타입·제약을 모델로 선언해 타입 안전과 자동 OpenAPI 문서화를 얻는다.
- **fluent `Validator` 체인 → Pydantic 검증 + 커스텀 validator.** 형식 검증(필수·길이·날짜·숫자)은 Pydantic 필드 제약으로, 비즈니스 규칙(`business_number` 10자리, `quantity` 정수 범위 등)은 `field_validator`/`model_validator`로.
- **검증·에러 메시지 문자열 자유화.** 원본 문자 동치 속박을 풀고 자유롭게 작성한다. 프론트가 메시지·코드로 분기하지 않으므로(아래 전제) 안전.

### 검증된 전제

frontend는 에러를 **메시지/코드/details로 분기하지 않는다**. `error.code === "..."` 분기 0건, `error.details` 소비 0건이며, 에러는 전부 `catch (e)`로 잡아 표시하거나 무시한다(`ErrorFallback.tsx`의 `error.message`는 React 에러 바운더리용으로 API 에러와 무관).
→ 검증 메시지·에러 코드·`details` 형태 자유화가 프론트에 무영향이다. 이것이 점진 전환을 슬라이스별로 안전하게 만드는 핵심이다.

## 외부 계약 불변식

점진 전환이 프론트 무영향이 되려면, 바꿔도 되는 것과 절대 불변인 것을 가른다.

| 구분            | 항목                                                                                                                                                                                                                                       |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **불변 (계약)** | 성공 envelope shape `{success, data, pagination?}` · 에러 envelope shape `{success, error: {code, message}}` · 에러 코드 체계(`VALIDATION_ERROR`/`NOT_FOUND`/`DUPLICATE_NAME`/`CONFLICT`/`SERVER_ERROR`) · 검증 실패 HTTP status = **400** |
| **자유 (구현)** | 검증 메시지 문자열 내용 · `details` 형태 · 내부 검증 방식(Validator → Pydantic) · request 모델 구조                                                                                                                                        |

Pydantic의 `ValidationError`(FastAPI 기본 422)는 **커스텀 예외 핸들러로 400 `{success, error: {code: "VALIDATION_ERROR", ...}}`로 변환**해 envelope shape와 status를 보존한다.

## 점진 전환 메커니즘

- **단위 = 도메인 슬라이스** — invoices / companies / items / settings / salespeople / sales_records / ocr.
- **트리거** — 해당 슬라이스에 신규 기능 또는 수정이 들어올 때, 그 슬라이스의 router + service + repository + tests를 목표 컨벤션으로 끌어올린다.
- **프론트 동시 수정** — 응답 envelope shape가 불변이므로 백엔드 슬라이스 현대화에 프론트 동시 수정은 대개 불필요하다. 단, 해당 슬라이스에 프론트 변경이 함께 필요한 기능이면 같이 처리한다.

## 골든 테스트의 재해석

골든 테스트(29파일 900+줄)는 폐기하지 않는다. 의미만 전환한다.

- **"원본 동치 검증" → "현재 API 계약 회귀 보호".** 검증 메시지·응답 포맷·부수효과를 고정하던 테스트는 이제 "현재 계약이 의도치 않게 깨지지 않게" 막는 회귀 테스트다.
- 슬라이스를 현대화하며 검증 메시지가 바뀌면, 해당 contract 테스트의 기대 문자열을 함께 갱신한다.
- `tests/fixtures/`의 "원본 TestData 포팅" 주석은 Phase 0에서 세탁한다(데이터 자체는 유지).

## Phase 0 — 즉시 일괄 정리 (동작 불변)

지금 일괄 처리한다. 모두 동작에 영향을 주지 않는다(modern 이미 고정).

1. **backend 출처 세탁** — PHP 결부 docstring·주석 57곳을 중립 서술로 바꾸거나 제거. `app/__init__.py`의 "원본 동형 포팅" 류 제거.
2. **frontend legacy 제거** — `api.ts` legacy 분기 9곳(~121줄) 제거: `ep()` → 고정 `/${resource}`, settings의 localStorage fallback, company/item update의 delete+create fallback, invoice duplicate fallback, export modern-only 체크 제거. `VITE_API_MODE` env·타입 제거. `types/api.ts`의 legacy 호환 필드(`total?`/`page?`/`limit?`) 제거. `mocks`의 ListResponse를 `pagination` 중첩 구조로 단일화(6곳).
3. **api-spec.json 정리** — PHP 동치 언급 6곳(헤더 description, conventions의 error-format/response-envelope/validation, ErrorEnvelope·InvoiceWriteRequest description) 중립화.
4. **명문화** — 루트·backend `AGENTS.md`와 `.claude/rules/api-conventions.md`에 목표 컨벤션 + 점진 규칙 + 외부 계약 불변식을 추가한다(미래 에이전트 기준). 이전 세션에서 AGENTS.md·api-conventions.md의 PHP 출처 언급은 이미 제거됨 — 여기에 "목표 컨벤션과 점진 전환 규칙"을 새로 더한다.
5. **문서** — README/CHANGELOG/CONTEXT는 과거형 서술이라 유지. 설계 정본 2개(contract-inventory, macmini-overview)는 역사 기록으로 보존.

PR 분리: `backend 출처 세탁` / `frontend legacy 제거` / `명문화` 를 리뷰 단위로 나눈다.

## 향후 (deferred)

- **검증 실패를 Pydantic 기본 422로 전환.** 당장은 외부 계약 보존을 위해 400을 유지하지만, 추후 프론트의 status 의존이 없음을 재확인한 뒤 Pydantic 표준 422 + 표준 에러 바디로 갈아탈 예정이다. 그 시점에 envelope 래핑 자체를 재검토한다.

## 리스크

- **점진 전환의 일관성 드리프트** — 슬라이스마다 현대화 시점이 달라, 한동안 "구식 슬라이스(Validator)"와 "신식 슬라이스(Pydantic)"가 공존한다. 외부 계약 불변식이 이를 봉합하지만, 어느 슬라이스가 전환됐는지 추적이 필요하다. → 명문화 문서에 전환 현황 체크리스트를 둔다.
- **details 형태 변경** — 프론트가 details를 안 쓰는 것을 확인했으나, 향후 프론트가 필드별 검증 표시를 도입하면 details 형태가 계약이 된다. 그 시점에 형태를 고정한다.

## 성공 기준

- Phase 0 후: backend/frontend/api-spec에서 원본 결부 출처 언급 0, frontend legacy 분기 0, 기존 테스트 전부 통과(동작 불변).
- 명문화 문서만 읽고도 다음 슬라이스를 목표 컨벤션으로 전환할 수 있다(외부 계약 불변식·전환 단위·검증 전략이 자족적으로 기술됨).
- 첫 슬라이스 전환 시: 해당 슬라이스가 Pydantic request 모델 + 400 envelope 유지로 동작하고, 프론트 무수정으로 통과.
