---
description: sjmj-ai invoice-ocr API 규칙 — api-spec.json 동기화, 라우터 구조, 무인증 경계, 구조화 envelope, contract 테스트 계약
paths:
  - "apps/invoice-ocr/backend/app/routers/**"
  - "apps/invoice-ocr/backend/app/main.py"
  - "apps/invoice-ocr/backend/app/core/**"
  - ".claude/ai-context/api-spec.json"
  - "VERSION"
  - "apps/invoice-ocr/backend/app/config.py"
---

# sjmj-ai invoice-ocr API 규칙

이 프로젝트의 백엔드 API는 FastAPI 서버다(`apps/invoice-ocr/backend`).
요청 흐름은 **router → service → repository** 3계층이며, 검증·응답·에러 계약은 contract 테스트로 고정한다.
전역 코딩/테스트/보안 규칙은 `~/.claude/rules/common/*` 에 있으니 여기서는 **이 프로젝트 고유의 API 표면 규약**만 다룬다.

## API 명세 동기화 (필수)

API 명세는 `.claude/ai-context/api-spec.json` (OpenAPI 3.0 기반)에서 관리한다. 엔드포인트의 Single Source of Truth.

**작업 전**: api-spec.json을 먼저 읽고 전체 표면을 파악한다.

- `x-api-overview.endpoints`: 엔드포인트 한 줄 스캔
- `x-api-overview.schema-usage`: 스키마 변경 시 영향 엔드포인트 역매핑
- `x-api-overview.conventions`: 호출 규약 (auth/error-format/envelope/예외 등)
- `paths` + `components.schemas`: 상세 정의

**작업 후**: 아래 변경이 발생하면 반드시 api-spec.json을 함께 갱신한다. **추가/제거뿐 아니라 기존 엔드포인트·스키마의 *수정*도 포함** — 구현이 바뀌었는데 명세가 그대로면 즉시 드리프트다.

- 엔드포인트 추가/제거/경로·메서드 변경 → `paths` + `x-api-overview.endpoints` 양쪽 반영
- **기존 엔드포인트 수정** (summary / query·path 파라미터 / HTTP 상태 코드 / error shape / 응답 envelope / 부수효과) → `endpoints[]` 해당 행 + `paths` 상세 동시 갱신
- 요청/응답 필드 추가·제거·타입·nullable·enum 변경 → `components.schemas` + `schema-usage` 확인
- 새 slice(라우터) 추가 또는 파일 이동 → `endpoints[]` 의 `router`, `source` 갱신
- Validator 규칙·검증 메시지 변경 → 해당 요청 스키마 description + 관련 테스트
- 루트 `VERSION` bump → `info.version` 동기 (아래 §버전 동기 참조)

## 라우터 구조

API 라우터는 모두 `app/routers/` 하위, `include_router(..., prefix="/api")` 로 마운트된다(`app/main.py`).

| Router        | 파일                           | Prefix               | 목적                                                 |
| ------------- | ------------------------------ | -------------------- | ---------------------------------------------------- |
| system        | `app/main.py`                  | (없음) / `/api`      | `/health`, `/api/health` 헬스체크                    |
| invoices      | `app/routers/invoices.py`      | `/api/invoices`      | 거래명세서 CRUD + duplicate + CSV export             |
| companies     | `app/routers/companies.py`     | `/api/companies`     | 거래처 자동완성 CRUD + 거래처별 명세서 목록          |
| items         | `app/routers/items.py`         | `/api/items`         | 품목 자동완성 CRUD                                   |
| salespeople   | `app/routers/salespeople.py`   | `/api/salespeople`   | 영업사원 CRUD(soft-delete)                           |
| settings      | `app/routers/settings.py`      | `/api/settings`      | 발급자 정보·도장 업로드·앱 설정                      |
| sales_records | `app/routers/sales_records.py` | `/api/sales-records` | 영업 실적 월별 집계·upsert·삭제                      |
| curation      | `app/routers/curation.py`      | `/api/curation`      | 큐레이션 검수 큐·잡 상세·쌍 큐레이션·검수완료·이미지 |

신규 slice 추가 시 위 4종(router+service+repository) + `tests/{contract,unit,integration}/` 3종 패턴을 그대로 따른다.

### 라우트 등록 순서 (중요)

- **API 라우터는 SPA catch-all(`GET /{full_path}`, `main._mount_static`)보다 먼저 등록**되어야 우선 매칭된다.
- invoices 내부에서도 `/invoices/export` 가 `/invoices/{id}` 보다 **먼저 선언**되어야 `export` 가 `{id}` 로 잡히지 않는다.

## 인증 & 경계

- **인증 없음.** 모든 엔드포인트 public — 토큰/세션/API key/미들웨어가 일절 없다.
  - 네트워크 경계(tailscale serve :8443, launchd :8400 로컬 바인드)로만 보호된다.
  - `app/db.py` 의 `token` 은 contextvar reset 토큰이지 인증 토큰이 **아니다** — 혼동 금지.
- 신규 엔드포인트도 기본 public. 인증을 도입하려면 먼저 `x-api-overview.conventions.auth` 와 각 엔드포인트 `auth` 필드 정책을 함께 설계한다.
- 페이로드 상한: 명시적 제한 없음(FastAPI 기본).

## 비즈니스 로직 분리

- HTTP 관심사(쿼리 파싱, 검증 호출, 응답 envelope, 상태 코드)는 **라우터**에.
- 비즈니스 로직 + 트랜잭션 경계는 **서비스**(`app/services/*_service.py`)에. `with db.transaction():` 으로 tx를 열면 내부 repo가 같은 conn을 공유한다.
- DB 접근은 **repository**(`app/repositories/*_repository.py`)에서만. 라우터·서비스에 raw SQL 직접 작성 금지.
- 라우터 핸들러는 **`sync def`** (FastAPI threadpool 실행). async 핸들러로 바꾸면 conn 바인딩 가정이 깨지므로 금지.

## 스키마 검증 (라우터 특화)

- 요청 body 는 `dict = Body(...)` **free-form** 으로 받고 `core/validators.Validator` (fluent)로 검증한다. **Pydantic 모델을 쓰지 않는 것은 의도된 선택** — 에러 메시지·`details` 형태를 문자 단위로 고정해 contract 테스트로 보호하기 위함이다.
- **점진 전환 중.** 위 free-form + Validator는 현재(미전환) 슬라이스의 방식이다. 슬라이스를 신규/수정할 때 Pydantic request 모델 + `field_validator`/`model_validator`로 전환하되, **검증 실패는 여전히 400 `VALIDATION_ERROR` + `{필드: 메시지}` details로 응답**해야 한다(`RequestValidationError` 핸들러가 422→400 변환). envelope shape·에러 코드·400 status·details 형태는 외부 계약 불변식이다.
- 검증 실패 → `bad_request(message, details)` → 400 `VALIDATION_ERROR`. `details` 는 `{필드: 메시지}` 맵.
- 검증 헬퍼: `required` / `max_length` / `date_format`(YYYY-MM-DD) / `business_number`(숫자 10자리) / `numeric` / `non_empty_array`.
- 검증 함수(`_validate_*`)는 라우터 파일 상단에 모아 create/update 가 공유한다.

## 에러 응답 형식

`core/errors.py` 단일 형태:

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "...",
    "details": { "field": "..." }
  }
}
```

| code             | status | 발생 위치                                                     |
| ---------------- | ------ | ------------------------------------------------------------- |
| VALIDATION_ERROR | 400    | `bad_request()` — 검증 실패, 잘못된 format/범위, 빈 body      |
| NOT_FOUND        | 404    | `not_found()` — 리소스 없음                                   |
| DUPLICATE_NAME   | 409    | items/salespeople 생성 시 UNIQUE 위반 graceful 처리(service)  |
| CONFLICT         | 409    | `conflict()` — 상태 충돌(OCR 잡 확정 가능 상태 위반 등)       |
| SERVER_ERROR     | 500    | 미처리 예외 전역 핸들러 (`str(exc)` 노출 — prod 운영 시 주의) |

- `details` 는 있을 때만 포함된다(검증 실패 시 필드 맵, 또는 sales-records year/month 범위 오류).
- 신규 도메인도 이 코드 체계를 따른다. 새 코드를 추가하면 `ErrorEnvelope.error.code` enum 도 갱신한다.

## HTTP 상태 코드 규약 (현재 사용)

| 상태 | 사용 시점                                                            |
| ---- | -------------------------------------------------------------------- |
| 200  | GET 성공, PUT 성공(body 있음), **DELETE 성공**(204 아님 — 아래 주의) |
| 201  | POST 자원 생성(`created`), invoice duplicate, sales-records upsert   |
| 400  | 검증 실패 / 잘못된 format·범위 / 빈 body                             |
| 404  | 리소스 없음                                                          |
| 409  | DUPLICATE_NAME (items/salespeople UNIQUE), CONFLICT (상태 충돌)      |
| 500  | 미처리 예외                                                          |

- **DELETE 는 204 가 아니라 200 + `{success, data:null, message}`** 를 반환한다. 새 DELETE 도 이 형태를 따른다.
- 401/403/422/429 는 현재 미사용(인증·rate limit 없음).

## 성공 응답 envelope 규약

`core/envelope.py`, jsonable_encoder 직렬화(date→`YYYY-MM-DD`, Decimal 처리). 응답 `data` 는 **DB 행 그대로 snake_case** — camelCase 변환 없음.

- 단일: `{ "success": true, "data": T }` (`single`, `created`)
- 컬렉션: `{ "success": true, "data": T[], "pagination": {page,limit,total,totalPages} }` (`list_response`)
- 삭제: `{ "success": true, "data": null, "message": "..." }` (`deleted`)

### envelope 예외 (역사적 계약 — 정리 대상 아님, 보존 대상)

1. **`GET /api/invoices/export`** — envelope 밖 **raw `text/csv`** (RFC 5987 `filename*=UTF-8''`). `format=xlsx` 는 라우터에서 받지만 ExportService 가 `ValueError` → 400 (현재 CSV만 구현).
2. **`GET /api/sales-records`** — 컬렉션 성격이지만 `single()` 로 `{salespeople, records}` 단일 집계를 반환. **pagination 없음, 목록 아님.**
3. **가짜 pagination** — `GET /api/companies/{id}/invoices`(page1/limit9999/totalPages1)와 `GET /api/salespeople`(page1/limit=total/totalPages1)은 전건을 반환하며 pagination 메타는 형식만 채운다.
4. **`GET /health`** — `/api` prefix 없이도 노출되고 envelope 미적용 raw `{status, version}`.
5. **`GET /api/curation/jobs/{job_id}/image/{kind}` · `GET /api/curation/jobs/{job_id}/crop/{row}`** —
   envelope 밖 **raw 이미지 바이트**(`FileResponse`). `job_id`/`row`(정수)·`kind`(enum `original|warped`)만 받아
   서버가 `SJMJ_DATA_DIR` 하위 경로를 조립한다(crop_ref 문자열을 raw 경로로 신뢰하지 않음 — path traversal 차단).
   **media_type:** `kind=warped`·`crop` 는 항상 `image/png`(서버에서 변환·저장). `kind=original` 은 업로드 포맷
   그대로(`image/jpeg` 또는 `image/png` — `FileResponse` media_type 미지정이므로 파일 확장자로 추론됨).
   없는 산출물(백필된 구 잡의 `warped.png` 등)은 404 에러 envelope.

## Pagination 방식

- offset 기반 `?page=N&limit=M` (invoices: limit 1~100 클램프, page≥1). 그 외 slice 는 위 가짜 pagination.
- cursor 기반은 미사용.

## URL 네이밍 규약

- 기본 REST: 복수 명사 컬렉션(`/invoices`, `/companies`, `/items`). kebab-case 컬렉션 허용(`/sales-records`).
- **RPC-style sub-action 허용(상태 전이/액션)**: `POST /invoices/{id}/duplicate`, `POST /settings/issuer/stamp`. 이는 의도된 예외다.
- `settings` 는 단일 리소스 그룹(`/settings/issuer`, `/settings/app`)으로 컬렉션이 아니다.

## API Versioning 정책

- URL 버전 prefix 없음(`/api/...` 단일). 외부 공개 계약이 아니라 동일출처 SPA 전용이므로 버전 분기 불필요.
- `info.version` 은 외부 버전이 아니라 **빌드 버전**: 루트 `VERSION` == `app/config.py:APP_VERSION` 과 동기되어야 한다(`test_version_sync.py` 검증, `scripts/sync-version.sh` 로 함께 갱신).
- Breaking change 시: 기존 API 계약(메시지·응답 포맷·부수효과)을 깨는지 먼저 확인하고 contract 테스트로 검증한다.

## Rate Limiting

- 미적용(내부망 단일 사용자 운영). 공개 API 확장 시 도입 검토.

## 특이 부수효과·계약 (놓치기 쉬운 것)

- **거래명세서 합계는 서버가 계산하지 않는다.** `total_supply/total_vat/grand_total` 과 품목 `supply/vat/total` 은 **클라이언트(프론트 invoice-form)가 계산해 전송**하며, 서버(service/repository)는 전송값을 그대로 저장하고 누락 시 0으로 저장한다. spec 기반 클라이언트가 이 필드를 생략하면 합계가 0으로 저장되므로 주의.
- `POST/PUT /api/invoices` 는 거래처·품목의 `usage_count` 를 증가시킨다. 명세 변경 시 이 부수효과 유지 여부를 명시한다.
- `POST /api/settings/issuer/stamp` 성공 응답 `data` 는 전체 `Issuer` 가 아니라 **`{stamp_image_url}` 만** 반환한다(`SettingsService.upload_stamp`). 400 사유: 파일 없음 / 500KB 초과 / PNG·JPG 외 타입.
- `POST /api/sales-records` 의 `snapshot_name` 은 **서버가 salesperson.name 으로 채운다** — 클라이언트 입력은 무시. UPSERT(`UNIQUE(salesperson_id, work_date)`).
- `quantity` 검증: bool 불가, 정수 문자열("100") 허용, 0~999,999,999.
- `DELETE /api/salespeople/{id}` 는 물리 삭제가 아니라 **soft-delete(is_active=0)**, message "비활성화되었습니다.".

## 테스트 (API 특화)

> 일반 TDD/coverage(80%) 규약은 `~/.claude/rules/common/testing.md` 참조. 실행 명령·DB 셋업은 루트 `CLAUDE.md` 참조.

- slice 당 `tests/{contract,unit,integration}/` 3종 구조 유지.
- 신규/수정 엔드포인트 필수 케이스: happy path + 4xx(검증 실패) + 해당 시 404/409. (401 는 인증이 없으므로 N/A.)
- 검증 메시지 문자열·응답 포맷·부수효과를 바꾸는 변경은 contract 테스트로 보호한다.

## PR 전 API 검증

> 빌드/CI 실행 명령은 루트 `CLAUDE.md` 참조.

- `app/routers/**`, `app/main.py`, `app/core/**` 가 변경된 PR은 **`.claude/ai-context/api-spec.json` diff 동반 필수**. 없으면 둘 중 하나:
  - (a) 명세 업데이트 누락 — 수정 필요
  - (b) API 계약 무영향 내부 리팩터 — PR description 에 "no API surface change" 명시
- **기존 엔드포인트 수정**은 `endpoints[]` 한 줄만 바뀌어도 반드시 함께 커밋한다.
- `jq empty .claude/ai-context/api-spec.json` 으로 JSON 유효성, `endpoints[]` 개수 == `paths` operation 개수 매칭 확인.
