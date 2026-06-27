# Phase 1B 계약 인벤토리 (FastAPI 포팅용)

- **날짜**: 2026-06-28
- **성격**: Phase 1B 포팅 착수 전 계약(API 표면) 정밀 인벤토리. 메인루프가 확정한 지반사실 위에서 실측 추출. 모든 항목은 `SJMJ-Web` 레거시 PHP 코드와 골든 테스트 실파일을 근거로 한다(근거 없는 단정 없음).
- **목적**: FastAPI 재구현이 "무엇을 동형으로 내야 프론트를 깨지 않고, 무엇이 골든 DoD인지"를 한 장에 고정한다.

## 0. 지반사실 요약 (재확인 완료)

| 항목 | 사실 | 근거 |
|---|---|---|
| 정본 라우터 | `backend/index.php` Front Controller, 30라우트/6리소스 | index.php:35-75 (직접 확인) |
| 구조화 envelope | `core/Response.php` — list는 `{success,data,pagination:{page,limit,total,totalPages}}` | Response.php:5-59 |
| 레거시 절차적 | `api/{invoices,companies,items,settings}.php` 병행 운영, 평평 envelope, 테스트 0 | .htaccess:19-21 (직접 확인) |
| 프론트 모드 | `VITE_API_MODE` 기본 `legacy`. `.env`/`.env.production` 둘 다 legacy | api.ts |
| 프론트 소비 | axios `.data`(body 전체) 반환 후 hook이 `res.data`·`res.total`만 읽음 | use-*.ts |
| salespeople/sales-records | 레거시 파일 **없음** → 배포에서도 항상 클린 URL(구조화) 호출 | .htaccess(rewrite 대상 아님) |
| 골든 | 228 테스트(24파일)가 구조화 컨트롤러/서비스/리포 경로 검증. 절차적 api/*.php 테스트 0 | tests/ |

**중심 충돌**: 골든(구조화 `{success,data,pagination}`) ↔ 배포 프론트 소비(평평 `{data,total}`).

---

## 1. 30라우트 마스터 표

레전드: M=modern(Front Controller 전용) · L=legacy(api/*.php 존재). 응답형태 — 구조화=`Response::*`, 평평=레거시 절차적. 골든=해당 라우트를 직접/간접 커버하는 컨트롤러 테스트 수(아래 §4 분류와 연동).

### invoices (7라우트) — DB: `invoices` + `invoice_items`

| # | Method | Path | modern 응답 | legacy 응답 | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 1 | GET | `/api/invoices` | `{success,data:[],pagination}` | `{data,total}` (L) | 200 | sort/order 기본, limit·page 0-클램프 | InvoiceController index |
| 2 | GET | `/api/invoices/export` | `text/csv`+BOM (envelope 밖) | 없음 | 200 | format∈{csv,xlsx}; 서비스는 csv만 | InvoiceController export(무효 format 거부) |
| 3 | GET | `/api/invoices/{id}` | `{success,data}` | `{data}` (L) | 200/404 | — | InvoiceController show/404 |
| 4 | POST | `/api/invoices` | 201 `{success,data}` | `{data}` (L) | 201/400 | issue_date·빈 items 거부 | InvoiceController store |
| 5 | PUT | `/api/invoices/{id}` | `{success,data}` | `{data}` (L) | 200/404 | items 교체 | InvoiceController update/404 |
| 6 | DELETE | `/api/invoices/{id}` | `{success,data:null,message}` | `{success:true}` (L) | 200/404 | — | InvoiceController destroy |
| 7 | POST | `/api/invoices/{id}/duplicate` | 201 `{success,data}` | **없음(M전용)** | 201/404 | 원본 없으면 404 | InvoiceController duplicate 201/404 |

### companies (6라우트) — DB: `company_suggestions`

| # | Method | Path | modern 응답 | legacy 응답 | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 8 | GET | `/api/companies` | `{success,data:[],pagination}` | `{data,total}` (L) | 200 | sort_by 기본/무효거부 | CompanyController index |
| 9 | GET | `/api/companies/{id}` | `{success,data}` | `{data}` (L) | 200/404 | — | CompanyController show/404 |
| 10 | POST | `/api/companies` | 201 `{success,data}` | `{data}` (L) | 201/400 | company_name·business# | CompanyController store |
| 11 | PUT | `/api/companies/{id}` | `{success,data}` | **L=delete+create(비원자)** | 200/404 | — | CompanyController update/404 |
| 12 | DELETE | `/api/companies/{id}` | `{success,data:null,message}` | `{success:true}`, `?id=` 쿼리 (L) | 200/404 | — | CompanyController destroy |
| 13 | GET | `/api/companies/{id}/invoices` | `{success,data}` | **없음(M전용)** | 200/404 | — | CompanyController invoices/404 |

### items (5라우트) — DB: `item_suggestions`

| # | Method | Path | modern 응답 | legacy 응답 | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 14 | GET | `/api/items` | `{success,data:[],pagination}` | `{data,total}` (L) | 200 | sort_by 기본/무효거부 | ItemController index |
| 15 | GET | `/api/items/{id}` | `{success,data}` | `{data}` (L) | 200/404 | — | ItemController show/404 |
| 16 | POST | `/api/items` | 201 `{success,data}` | `{data}` (L) | 201/400 | item_name | ItemController store |
| 17 | PUT | `/api/items/{id}` | `{success,data}` | **L=delete+create(비원자)** | 200/404 | — | ItemController update/404 |
| 18 | DELETE | `/api/items/{id}` | `{success,data:null,message}` | `{success:true}` (L) | 200/404 | — | ItemController destroy |

### settings (5라우트) — DB: `issuers`, `app_settings`

| # | Method | Path | modern 응답 | legacy 응답 | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 19 | GET | `/api/settings/issuer` | `{success,data}` | `{data}` (L, GET) | 200/404 | — | SettingsController issuer get/404 |
| 20 | PUT | `/api/settings/issuer` | `{success,data}` | **L=POST `saveIssuer`**, stamp_image_url 컬럼 누락 | 200/400 | company_name·business# | SettingsController update |
| 21 | POST | `/api/settings/issuer/stamp` | `{success,data:{stamp_image_url}}` | **없음(M전용)** | 200/400/404/500 | multipart, 500KB, PNG/JPG | SettingsController stamp(no-file/issuer-404) |
| 22 | GET | `/api/settings/app` | `{success,data}` | **없음 → 프론트 localStorage** | 200 | — | SettingsController app get |
| 23 | PUT | `/api/settings/app` | `{success,data}` | **없음 → 프론트 localStorage** | 200/400 | 빈 body 거부 | SettingsController app update |

### salespeople (4라우트, M전용·클린 URL) — DB: `salespeople`

| # | Method | Path | modern 응답 | legacy | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 24 | GET | `/api/salespeople` | `{success,data:[],pagination}` | — | 200 | — | SalespersonController index |
| 25 | POST | `/api/salespeople` | 201 `{success,data}` | — | 201/400 | name trim·제어문자·중복활성 거부 | SalespersonController store 201/name |
| 26 | PUT | `/api/salespeople/{id}` | `{success,data}` | — | 200/404 | 중복검사 self 제외 | SalespersonController update 404 |
| 27 | DELETE | `/api/salespeople/{id}` | `{success,data:null,message}` | — | 200/404 | soft-delete(is_active) | SalespersonController destroy/404 |

### sales-records (3라우트, M전용·클린 URL) — DB: `sales_records`

| # | Method | Path | modern 응답 | legacy | 상태 | 검증 | 골든 |
|---|---|---|---|---|---|---|---|
| 28 | GET | `/api/sales-records` | `{success,data}` (월별 MonthlySalesData 단건) | — | 200/400 | year_month 검증 | SalesRecordController index |
| 29 | POST | `/api/sales-records` | 201 `{success,data}` | — | 201/400 | upsert, 음수 qty·잘못된 날짜 거부 | SalesRecordController store |
| 30 | DELETE | `/api/sales-records/{id}` | `{success,data:null,message}` | — | 200/404 | FK RESTRICT | SalesRecordController destroy/404 |

> 합계 30라우트. modern 전용(레거시 부재) = #2 export, #7 duplicate, #13 company invoices, #21 stamp, #22·23 app settings, #24-30 salespeople/sales-records.

---

## 2. 리소스별 상세 (프론트 소비 필드 · DB · 폴백)

핵심 원리: 프론트는 axios `.data`(=body 전체)를 반환하고 hook/store가 그 body에서 **최상위 `res.data`** 와 **`res.total`(invoices 목록 1곳)** 만 읽는다.

| 리소스 | 배포 호출 경로 | 읽는 필드 | DB 테이블 | 폴백·우회 |
|---|---|---|---|---|
| invoices | legacy 평평 `/invoices.php` | `res.data`(목록·단건), `res.total`(use-invoices.ts:34, number 가드) | invoices+invoice_items | duplicate=클라 getById+create; export=legacy throw 비활성 |
| companies | legacy 평평 `/companies.php` | `res.data`만 | company_suggestions | update=delete+create(비원자, 실패 시 data 손실); delete `?id=` |
| items | legacy 평평 `/items.php` | `res.data`만 | item_suggestions | update=delete+create(비원자) |
| settings | legacy 평평 `/settings.php` | `res.data`(issuer·app) | issuers, app_settings | app settings=localStorage fallback(서버 미사용); stamp=issuer.stamp_image_url에 base64 인라인 |
| salespeople | **클린 URL `/salespeople`(구조화)** | `res.data`만 (`.total`·`.pagination`·`.success`·`.message` 미소비) | salespeople | 페이지네이션 UI 없음 |
| sales-records | **클린 URL `/sales-records`(구조화)** | `res.data`만 | sales_records | — |

**결론**: salespeople/sales-records는 이미 운영에서 구조화 envelope을 받아도 `res.data`만 꺼내 정상 동작한다(구조화도 data는 최상위). modern 구조화가 프론트를 깨는 지점은 **오직 use-invoices.ts:34의 평평 `res.total`** — 구조화에선 그 값이 `pagination.total`로 들어가 `res.total=undefined`→가드 실패→`total=0`→invoices 페이지네이션만 깨진다(목록 data 자체는 표시됨). 에러 경로는 어떤 hook도 응답 body를 파싱하지 않고 axios가 throw한 `Error.message` 또는 한국어 fallback만 쓰므로, 에러 envelope 형태 불일치는 실제로 프론트를 깨지 않는다.

### 프론트 TS 타입 (frontend/src/types/api.ts) — 전부 평평·success 없음

```ts
interface ListResponse<T>  { data: T[]; total: number; page: number; limit: number; }  // total 최상위
interface SingleResponse<T>{ data: T; message?: string; }
interface ErrorResponse    { error: string; message: string; status: number; }          // import조차 안 됨(미소비)
```

---

## 3. 비-JSON 2표면 계약

### 3.1 도장 업로드 (stamp upload) — 고아 표면

- **라우트**: `POST /api/settings/issuer/stamp` → `SettingsController@uploadStamp` (index.php:62). MODERN 전용. 레거시 `api/settings.php`엔 없고 .htaccess도 `^api/settings\.php$`만 rewrite.
- **요청**: `multipart/form-data`, 파트명 `image` (`$req->file('image')` → `$_FILES['image']`). 누락 시 400 `이미지 파일이 필요합니다.`
- **저장**: `backend/uploads/stamps/` (없으면 mkdir 0755). 파일명 `stamp_{issuerId}.{ext}` (png/jpg, issuer당 고정·덮어쓰기). 반환 URL `/uploads/stamps/{filename}`. issuers.stamp_image_url 컬럼에 영속화.
- **응답**: 구조화 200 `{success:true, data:{stamp_image_url:"/uploads/stamps/stamp_{id}.png"}}`. issuer 미존재 시 404.
- **검증**(FileUpload): UPLOAD_ERR_OK, size ≤ 500KB, MIME ∈ {png,jpeg}. 위반 시 400. 프론트(stamp-upload.tsx)도 동일 규칙을 클라 검증하나 서버로는 안 보냄.
- **프론트 호출**: **0건(고아)**. stamp-upload.tsx는 `FileReader.readAsDataURL`로 base64 data URL을 만들어 `issuer.stamp_image_url`에 인라인 → `saveIssuer`(JSON)로 전송. 멀티파트 업로드 경로는 어떤 프론트도 도달 못 함.
- **치명 충돌**: 배포 기본 legacy에서 `saveIssuer`는 `POST /api/settings.php`로 가는데, 그 INSERT/UPDATE 컬럼 목록에 `stamp_image_url`이 **없어** → 레거시 모드에서 도장 data URL은 **조용히 유실**된다.

### 3.2 거래명세서 export (CSV 다운로드)

- **라우트**: `GET /api/invoices/export?format=&date_from=&date_to=&company_id=` → `InvoiceController@export` (index.php:37). `/{id}`보다 먼저 등록되어 매칭. MODERN 전용.
- **응답**: `text/csv; charset=utf-8`, 200, envelope 밖(header 직접 출력 + `php://output`). HttpResponseException 미throw라 Front Controller가 JSON 미첨부.
- **Content-Disposition**: `attachment; filename="거래명세서_{오늘날짜}.csv"` (필터·포맷 무관 고정).
- **본문**: UTF-8 BOM + fputcsv. 헤더행=[ID,문서제목,발행일,거래처,거래처2,차량번호,메모,공급가액,부가세,합계,생성일]. `sanitizeCsvField`로 CSV formula injection 방지(`=+-@\t\r` 선두값에 `'` prefix).
- **포맷**: 컨트롤러 화이트리스트 {csv,xlsx}이나 ExportService가 csv 외엔 `InvalidArgumentException`→400. **실질 csv만**(xlsx는 유령 포맷).
- **프론트 호출**: `_realInvoiceAPI.export` (`responseType:'blob'`). 단 첫 줄 `if (API_MODE!=='modern') throw` → 배포 기본 legacy에선 백엔드 도달 전 클라 throw.
- **충돌**: (1) export는 modern 전용 — 배포 legacy에선 항상 클라 throw, CSV 스트림 미도달. (2) xlsx 유령. (3) raw text/csv+BOM이라 envelope 파싱하면 깨짐(blob만 안전). (4) 파일명 필터 무관 고정.

---

## 4. 골든 228 분류표

총 228 테스트 / 24 파일. 계층별 합계: Core 36, Config 2, Controller 67, Service 58, Repository 65.

| 파일 | 개수 | 계층 | 이식성 | 초점 |
|---|---|---|---|---|
| ResponseTest | 9 | Core | 재작성 | 구조화 envelope helper 자체 단언. 평평 택하면 통째 재작성 |
| RouterTest | 8 | Core | 재작성 | 수제 Router dispatch/{id}/404/405. FastAPI가 대체, 404/405 스모크만 잔존 |
| ValidatorTest | 15 | Core | **직접 재사용** | required/maxLength/dateFormat/businessNumber/numeric. pydantic 이식 |
| HttpResponseExceptionTest | 4 | Core | 재작성 | golden 캡처 캐리어. HTTPException으로 대체(obsolete) |
| AppConfigTest | 2 | Config | 재작성 | env override. pydantic BaseSettings |
| CompanyControllerTest | 14 | Controller | 값재사용 | mock service, success+data+error.code |
| InvoiceControllerTest | 19 | Controller | 값재사용 | sort/limit·page 클램프, duplicate 201/404, export 무효 format |
| ItemControllerTest | 11 | Controller | 값재사용 | sort_by, store/destroy 404 |
| SalespersonControllerTest | 6 | Controller | 값재사용 | index/store 201/update 404/destroy |
| SalesRecordControllerTest | 6 | Controller | 값재사용 | 월별 index, upsert, destroy 404 |
| SettingsControllerTest | 11 | Controller | 값재사용 | issuer get/update, stamp(no-file/404), app get/update |
| CompanyServiceTest | 11 | Service | 값재사용 | getList 평평 반환 {data,pagination} |
| ExportServiceTest | 2 | Service | **직접 재사용** | sanitizeCsvField 순수함수 |
| InvoiceServiceTest | 13 | Service | 값재사용 | pagination·totalPages, duplicate, items 교체 |
| ItemServiceTest | 9 | Service | 값재사용 | getList pagination, CRUD |
| SalespersonServiceTest | 7 | Service | 값재사용 | trim·제어문자·중복차단 도메인 규칙 |
| SalesRecordServiceTest | 4 | Service | 값재사용 | snapshot_name 캡처, 미등록 거부 |
| SettingsServiceTest | 12 | Service | 값재사용 | tel_fax 조립, upsert, app-settings key 무시 |
| CompanyRepositoryTest | 13 | Repository | **직접 재사용** | 실 MySQL+tx롤백. upsert/검색/정렬/incrementUsage |
| InvoiceRepositoryTest | 17 | Repository | **직접 재사용** | insert+items, pagination, CASCADE, findAllForExport |
| ItemRepositoryTest | 12 | Repository | **직접 재사용** | category 필터, incrementUsage |
| SalespersonRepositoryTest | 6 | Repository | **직접 재사용** | soft-delete, findActiveByName |
| SalesRecordRepositoryTest | 7 | Repository | **직접 재사용** | upsert, 월스코프, FK RESTRICT |
| SettingsRepositoryTest | 10 | Repository | **직접 재사용** | issuer upsert, updateStampUrl, key-value |

### 이식성 3갈래 (총 228)

- **직접 재사용 ≈ 82 (36%)**: Repository 65 + Validator 15 + Export 2. SQL 의미·규칙·순수함수가 그대로. schema-test.sql + TestData 팩토리는 pytest 골든 데이터로 직접 재활용, tx-롤백 격리는 pytest fixture 1:1. 단 실 DB 하니스 전제(난이도 중).
- **값만 재사용 ≈ 121 (53%)**: Controller 67 + Service 54. 의도·픽스처·상태/에러코드는 살지만 하니스·envelope 단언 재배선. Controller가 **envelope 결정에 가장 민감**(최대 리스크 구간).
- **재작성 ≈ 25 (11%)**: ResponseTest 9 + RouterTest 8 + HttpResponseException 4 + AppConfig 2. FastAPI/pydantic 플러밍이 대체.

### envelope 결합 지점 (평평 전환 시 재작성 ≈ 84)

`assertSuccessResponse`가 `body['success']===true`, `assertErrorResponse`가 `body['error']['code']`를 단언(합계 75회), `body['data']` 중첩 35회. **pagination 중첩(`body['pagination']`)을 출력으로 직접 단언하는 건 ResponseTest::testListResponse 단 1곳뿐** — Controller index는 mock 서비스 RETURN 픽스처로만 pagination을 쓰고 출력은 data 카운트만 본다. 평평 전환 영향 집합: ResponseTest 9 + RouterTest 8 + Controller 67 = 84. Service 58·Repository 65·Validator 15·Export 2는 envelope 비결합(평평/구조화 무관).

---

## 5. 계약 충돌 목록

| id | 설명 | 심각도 | 제안 해소 |
|---|---|---|---|
| C1-stamp-legacy-loss | 배포 기본 legacy에서 `saveIssuer`(POST settings.php) INSERT/UPDATE에 `stamp_image_url` 컬럼 없음 → 도장이 조용히 유실. 멀티파트 stamp 엔드포인트는 어떤 프론트도 호출 안 함(고아) | CRITICAL | FastAPI는 issuer upsert에 stamp_image_url 포함(현 modern 컬럼 보존). data URL 인라인 경로를 1급 계약으로 명시하거나 멀티파트로 통일 결정 필요 |
| C2-total-nesting | use-invoices.ts:34가 최상위 `res.total`을 읽음 / 구조화는 `pagination.total`에 중첩 → 구조화 전환 시 `total=0`, invoices 페이지네이션만 깨짐(실 운영 깨짐은 이 1곳뿐) | HIGH | 택1: (A) 평평 envelope로 total 최상위 유지 → 프론트 무변경; (B) 구조화 유지 + use-invoices를 `pagination.total`로 1줄 수정 |
| C3-flat-vs-structured-list | 프론트 ListResponse `{data,total,page,limit}` 평평 / PHP list `{success,data,pagination}` 구조화 — 형태 불일치 | HIGH | envelope 전략 결정(§중심결정). C안=클린URL+평평이면 형태 일치 |
| C4-legacy-second-contract | api/*.php 평평 계약(list `{data,total}`, error `{error:'msg'}`, delete `{success:true}`)이 골든과 다른 제2 계약. 테스트 0. 배포 프론트가 inv/comp/item/settings에 실제 의존 | HIGH | FastAPI가 어느 계약을 정본화할지 확정. 평평 채택 시 골든 envelope 단언 84개 재작성 |
| C5-feature-gate-coupling | duplicate/export/원자적 PUT update/서버 app settings가 프론트 `API_MODE==='modern'`으로만 활성. legacy에선 duplicate=클라우회, export=throw, update=delete+create(비원자·data 손실), app settings=localStorage | HIGH | modern 기능을 살리려면 프론트를 modern으로 플립해야 함 → envelope 결정과 묶임 |
| C6-nonatomic-update | legacy company/item PUT = delete+create. 실패 시 레코드 소실(api.ts:158 경고) | MEDIUM | FastAPI는 원자적 UPDATE 제공. 프론트가 modern PUT을 쓰도록 전환 |
| C7-xlsx-ghost | export format=xlsx가 컨트롤러 화이트리스트엔 있으나 서비스가 400 거부 → csv만 실동작 | MEDIUM | xlsx 제거 또는 구현. FastAPI는 csv만 노출 권장 |
| C8-export-not-json | export 응답이 envelope 밖 raw text/csv+BOM. 무변경 프론트가 envelope 파싱하면 깨짐(blob만 안전) | MEDIUM | FastAPI도 StreamingResponse(text/csv)로 동형 유지, JSON 래핑 금지 |
| C9-success-field | 구조화는 모든 응답에 `success` / 프론트 TS엔 없음(미소비) | LOW | success 추가돼도 무해(무시됨). 결정 영향 없음 |
| C10-error-shape-nominal | PHP error `{success:false,error:{code,message}}` / 프론트 ErrorResponse `{error:string,...}` 평평. 단 ErrorResponse는 import조차 안 됨, hook은 axios Error.message만 읽음 | LOW | 명목 충돌. 런타임 무영향. FastAPI는 골든 error.code 보존 권장(ML/외부 소비자용) |

---

## 중심 결정 — FastAPI envelope 전략 (A / B / C)

§5의 C3·C5가 가리키는 결정. 프론트가 읽는 필드가 `res.data`(전부)+`res.total`(invoices 1곳)뿐이라, 세 안의 실질 차이는 **(1) 골든 충실도, (2) 프론트 수정 범위, (3) 기능 완전성**으로 압축된다.

| 기준 | A. 레거시 평평 포팅 | **B. modern 구조화 포팅** | C. 클린URL + 평평 envelope |
|---|---|---|---|
| FastAPI 응답 | `{data,total}` 평평 | `{success,data,pagination}` 구조화 | 클린URL + `{data,total}` 평평 |
| 프론트 모드 | legacy 유지(무수정) | modern 플립 | modern 플립 |
| 프론트 깨짐 | 없음 | **단 1곳**(use-invoices `res.total`) | 없음(envelope 코드 무수정) |
| 골든 매핑 | ✗ 4/6 리소스 테스트 0 | **✓ 228 최대 충실** | △ Controller 67 평평 재배선 |
| 기능 완전성 | ✗ 비원자 update·export 무·stamp 유실·app settings localStorage | **✓ 전 기능** | ✓ 전 기능 |
| 트레이드오프 | 무수정이나 **검증 안 된 열화 계약을 정본으로 굳힘** | 프론트 소수 수정 ↔ golden·기능 보존 | envelope 무수정이나 success/error 없는 평평을 ML/외부에 강제 |

> **추천: B(modern 구조화 포팅).** 근거 — (1) 로드맵이 "선택 아님"으로 못박은 *골든=DoD*에 최대 충실(228이 구조화를 인코딩). (2) 원본의 *의도된* 전 기능을 복원하며 legacy 모드의 잠재 버그(C1 도장 유실, C6 update 데이터 손실, 설정 미영속)를 함께 해소. (3) 프론트 divergence가 구조적으로 최소(envelope 깨짐 C2 1곳 + §4의 선재 드리프트 몇 곳 — 클린URL 포팅이면 어느 안이든 마주쳐야 함). (4) Phase 2 ML/외부 소비자에게 깨끗한 success/error 계약 제공.
>
> 대가: "무변경 프론트(base URL만 조정)"의 글자 그대로는 아니며, 1C에서 **`VITE_API_MODE=modern` 플립 + 소수 국소 수정**(ListResponse 타입+use-invoices total, DELETE `?id=`, invoices sort_by 매핑, items 카테고리 어휘, vat 타입)이 필요하다. 다수가 버그 수정 성격. 이 수정 목록은 1B 산출의 일부로 1C에 핸드오프한다.

---

## 6. 골든 DoD 재정의 (바이트매치 불가 시)

PHP→FastAPI 포팅에서 골든 228은 **바이트 단위 동일 출력으로 비교 불가**다(언어·직렬화·envelope 결정이 달라짐). 따라서 "골든 비교=DoD"를 다음 3축 대조로 재정의한다.

1. **값 동치 (value parity)**: 비즈니스 반환값·계산 결과가 동일. Service/Repository 계층(123개)은 `data`·`pagination` 키 값, totals, tel_fax 조립, snapshot_name, incrementUsage 누적, CASCADE/FK RESTRICT 동작을 값으로 대조 — envelope과 무관해 가장 충실한 골든.
2. **상태코드·에러코드 동치**: 200/201/400/404/500 + `error.code`(VALIDATION_ERROR/NOT_FOUND/SERVER_ERROR) 의미 보존. Controller 67개의 핵심 자산. envelope 형태가 평평으로 바뀌어도 상태코드·에러 식별자는 동일해야 DoD 충족.
3. **검증규칙 동치**: Validator 15 + 도메인 규칙(name trim·제어문자·중복활성 거부, 음수 qty, year_month, business# 패턴)을 동일 입력→동일 합/거부로 대조. pydantic으로 값 이식.

**+ envelope reshape 명세**: §중심결정에서 평평/구조화를 확정한 뒤, 그 한 가지 형태를 "정본 envelope"로 고정하고 골든의 envelope 단언 84개를 그 스키마로 일괄 재배선한다. DoD = (값·상태·검증 동치) AND (정본 envelope 스키마 준수). 바이트매치가 아니라 **계약 동치**가 DoD다.

---

## 7. ML 이음새 (backend config.py 통합)

기존 ML 트랙(`apps/invoice-ocr/ml/ocr_poc/config.py`)은 이미 `SJMJ_DATA_DIR`·`SJMJ_DB_BACKUP`을 경계에서 검증(미설정 시 RuntimeError)하는 규약을 가진다. Phase 1B FastAPI backend(`apps/invoice-ocr/backend/app/config.py`)는 현재 `SJMJ_PORT`·`SJMJ_STATIC_DIR`만 다룬다. 통합 env 규약:

| env | 용도 | 검증 | 출처 |
|---|---|---|---|
| `SJMJ_DATA_DIR` | 데이터 루트(images/labels/references) | 부재 시 RuntimeError | ml/ocr_poc/config.py 동형 이식 |
| `SJMJ_DB_BACKUP` | MySQL 덤프(.sql) 경로 | 부재 시 RuntimeError | 동상 |
| `SJMJ_DB_*` (신규) | 운영 MySQL 연결(HOST/PORT/NAME/USER/PASS) | pydantic BaseSettings, 빈 PASS 존중(AppConfigTest 골든) | PHP config/app.php DB_* 동치 |
| `SJMJ_PORT` | API 포트(기본 8400) | ValueError→기본값 | 현 backend/config.py 유지 |
| `SJMJ_STATIC_DIR` | 프론트 dist | 존재 시만 | 현 backend/config.py 유지 |

규약: `ocr_poc`와 동일하게 **경계에서만 env 해석**(코드 절대경로 하드코딩 금지), worker/ml 컨테이너에 동일 env 주입. `SJMJ_DB_BACKUP`은 정본화된 DB(.sql)를 가리키고, 신규 `SJMJ_DB_*`는 런타임 MySQL 연결을 가리킨다(둘은 별개 — 백업 경로 vs 라이브 커넥션). pydantic BaseSettings로 AppConfigTest 골든(env override + 빈 PASS 존중)을 재현한다.

---

## 8. vertical slice 권고

**invoices**를 vertical slice 1번으로 관통한다. 이유: invoices만이 (1) 구조화 vs 평평 충돌이 실제 런타임 깨짐으로 발현되는 **유일한 리소스**(C2-total-nesting), (2) modern 전용 비-JSON 표면(export CSV)과 전용 엔드포인트(duplicate)를 동시에 보유, (3) 7라우트 + invoice_items 조인 + pagination + CASCADE로 가장 복잡한 계약을 담는다. 즉 invoices를 끝까지 관통하면 "평평이냐 구조화냐"·"기능 게이트"·"비-JSON envelope 밖 응답"·"실 DB tx" 4대 가정을 한 번에 반증/검증한다. companies/items는 invoices의 축소판이라 invoices가 통과하면 기계적으로 따라온다.
