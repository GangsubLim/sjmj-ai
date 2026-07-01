# Phase B 큐레이션 검수 페이지 — plan eng-review (Step 2)

리뷰 대상 plan: `docs/superpowers/plans/2026-07-01-curation-review-page-phase-b.md`
정본 spec: `docs/superpowers/specs/2026-07-01-curation-review-page-phase-b-design.md`
리뷰어 관점: 엔지니어링 매니저 (좌표 정확성·불변식·task 분해·테스트 전략)
리뷰 방식: 핵심 주장 코드/ api-spec 표본 검증 + 비대칭 계약 검증

---

## 종합 판정: **Go-with-fixes**

아키텍처, 좌표 정확성, 계약 비대칭 모델링, TDD 분해, URL 빌더 standalone 결정은 코드·api-spec
대조에서 모두 견고하다. 외부 계약 불변식(엔드포인트 6종·envelope·비대칭)은 정확히 보존된다.
다만 **plan이 직접 제시한 단위 테스트 2건이 같은 plan이 제시한 구현 코드와 어긋나** RED→GREEN의
"Expected: PASS"가 거짓이다(H1·H2). 둘 다 국소 수정으로 해소 가능하고 구조 변경이 아니므로
rework가 아니라 fix-then-go다. 추가로 라벨 commit 경로의 이중 발화(M1)·옵티미스틱 동시성
롤백(M2)을 Task 4/6에서 손봐야 한다.

핵심 강점(검증됨):

- api-spec `CurationPair`는 `job_id`·`top5`를 **한 스키마**에 두고 주석으로 "언제 포함"을 명시한다.
  plan은 이를 `CurationPairBase` + `CurationJobPair(+top5)` + `CurationPairPatchResult(+job_id)`로
  쪼개 **비대칭을 타입으로 강제**한다 — api-spec보다 더 정밀하고, 옵티미스틱 merge의 top5 보존
  로직과 정확히 맞물린다.
- URL 빌더 standalone(`curationImageUrl`/`curationCropUrl`) 결정은 spec의 `curationAPI.imageUrl`보다
  **실제로 더 옳다**(아래 L2). `createMockProxy`가 객체 전체를 감싸므로, URL 빌더를 proxied
  객체에 두면 mock 모드에서 string이 아니라 Promise를 반환해 `<img src>`가 깨진다.

---

## 심각도별 이슈

| #   | 이슈                                                                                                                                                                                         | 심각도 | 근거(파일:라인 / api-spec)                                                                                                                                                                                                                                                                             | 권고                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| H1  | Task 5 단위 테스트가 `getByText("미검수")`로 단언하지만 구현은 `"● 미검수"`를 렌더 → 못 찾고 throw. "Expected: PASS (2 tests)" 거짓                                                          | HIGH   | plan Task5 Step1 test(L940) `getByText("미검수")` vs Step3 impl(L1040) `<span>● 미검수</span>`. e2e(Task8 L1653)는 `"● 미검수"` 사용 → 단위 테스트만 불일치                                                                                                                                            | 단위 테스트를 `getByText("● 미검수")` 또는 `getByText(/미검수/)`(정규식=substring)로. e2e와 문자열 SSOT 통일                       |
| H2  | Task 6 단위 테스트 `getByText(/잡 #128/)`이 PageHeader `<h1>`와 본문 `<h1>` **두 개**에 매칭 → "multiple elements" throw                                                                     | HIGH   | `PageHeader.tsx`는 `<h1>{title}</h1>` 렌더, 컨테이너 `className="z-20 lg:hidden"`. plan Job page는 `<PageHeader title={`잡 #${job.job_id}`}/>`(L1449) + 본문 `<h1>잡 #{job.job_id}</h1>`(L1452). jsdom은 CSS 미적용 → 두 h1 모두 존재. `getNodeText`는 직속 텍스트노드만 보므로 둘 다 `"잡 #128"` 매칭 | 본문 h1만 두고 PageHeader title 중복 제거, 또는 테스트를 `getByRole("heading",{level:1})` 후 가시성 필터/단일 소스로. (M3와 연동)  |
| M1  | 라벨 자동완성 선택 시 `onCommit` 이중 발화 — 팝오버 항목 클릭하면 input blur→`onCommit(inputValue)`(타이핑값) 먼저, 이어서 `onSelect`→`onCommit(s.label)`. patchPair 2회(첫 번째는 stale 값) | MEDIUM | plan Task6 Step5: input에 `onBlur={()=>onCommit?.(inputValue)}`(L1201) + CommandItem `onSelect` 끝에 `onCommit?.(s.label)`(L1213). `CurationPairRow.commitLabel`(L1339) 가드는 `!= canonical`뿐이라 타이핑 중간값도 통과 가능                                                                          | 선택 진행 플래그(mousedown 가드)로 blur-commit 억제, 또는 onCommit을 blur 단일 경로로. spec "blur 시 옵티미스틱 PATCH"와 정합 유지 |
| M2  | 옵티미스틱 동시 PATCH 롤백 경쟁 — `patchPair` 스냅샷을 호출별로 잡고 실패 시 그 스냅샷으로 복원. 라벨+제외를 연속 발행하면 두 번째가 첫 번째 결과를 스냅샷 → 한쪽 실패 시 lost-update        | MEDIUM | plan Task4 Step3 `setJob((prev)=>{snapshot=prev; ...})`(L818) + catch `if(snapshot) setJob(snapshot)`(L842). spec §3.3은 단건 흐름만 규정, 동시성 미규정                                                                                                                                               | 필드별 부분 롤백(실패한 pair의 변경 필드만 되돌리기) 또는 in-flight 직렬화. 관리자 도구 저빈도라 LOW로 강등 가능하나 명시 권장     |
| M3  | `PageHeader`는 `lg:hidden`(모바일 전용)인데 본 기능은 데스크톱 우선 관리자 도구 → 데스크톱에선 죽은 노드. jsdom 테스트만 깨고(H2) UI 가치 0                                                  | MEDIUM | `PageHeader.tsx` 컨테이너 `z-20 lg:hidden`. spec §1 "데스크톱 우선"                                                                                                                                                                                                                                    | 큐/잡 페이지에서 PageHeader 제거하고 본문 헤더만(다른 데스크톱 화면 패턴과 정합). H2도 함께 해소                                   |
| L1  | Risk #3(reviewJob ack 형태 가정)은 이미 api-spec이 확정 — 미해결 가정 아님                                                                                                                   | LOW    | api-spec `/api/curation/jobs/{job_id}/review` 200 `data:{job_id,curation_reviewed}`. plan mock/타입 일치                                                                                                                                                                                               | 리스크 목록에서 "가정"→"확정"으로 격하. 코드 변경 불필요                                                                           |
| L2  | URL 빌더 standalone 명명이 spec `curationAPI.imageUrl/cropUrl`과 어긋남                                                                                                                      | LOW    | plan L44 설계결정 vs spec §3.2 L99                                                                                                                                                                                                                                                                     | **plan이 옳다**(mock proxy 오염 회피). 변경은 plan이 아니라 **spec을 갱신**해 SSOT 드리프트 차단                                   |
| L3  | e2e가 mock 시드 `row_index 1`(pair 9002) 존재에 결합(Risk #5)                                                                                                                                | LOW    | plan Task8 `getByLabel("행 1 라벨")`, 시드 #128 pairs row_index 0·1                                                                                                                                                                                                                                    | mock e2e 한정이라 허용. 시드·셀렉터 동기 주석 1줄로 충분                                                                           |
| L4  | `getApiBaseUrl()` 기본값 `:8000`인데 런타임 dev/prod는 `:8400`(/api 프록시)                                                                                                                  | LOW    | `services/api.ts:29` 기본 `http://localhost:8000/api` vs `vite.config.ts` 프록시 `:8400`                                                                                                                                                                                                               | 기존 이슈(plan 책임 아님). URL 빌더 테스트는 substring/endsWith라 통과. dev에서 실이미지 직링크 시 8000 발산 가능 — 별건 메모만    |

CRITICAL: 없음(데이터 손실·보안·외부 계약 위반 없음).

---

## 코드 표본 검증 결과

| 검증한 plan 주장                                                                                   | 결과                | 근거                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `services/api.ts` `ocrAPI` real-only export 선례(307)                                              | 통과                | `services/api.ts:307 export const ocrAPI = _realOcrAPI;`                                                                                                   |
| `createMockProxy`(322-338)/`getMock`(317-320)/`getApiBaseUrl`(22-30)                               | 통과                | 실제 322-338 / 317-320 / 22-30 정확 일치                                                                                                                   |
| `createMockProxy`가 URL 빌더 객체-메서드를 깨뜨린다는 전제(standalone 정당성)                      | 통과                | proxy `get`은 USE_MOCK 시 async wrapper 반환(L331-335) → string 못 돌려줌. standalone 결정 타당                                                            |
| `types/api.ts` `ListResponse{data:T[],pagination?}`/`SingleResponse{data:T,message?}`/`Pagination` | 통과                | `types/api.ts:1-19` 일치                                                                                                                                   |
| `main.tsx` lazy children 패턴, sales-performance 뒤 삽입                                           | 통과                | `main.tsx:13-16` lazy 블록, `:24-66` children, `:56-63` sales-performance                                                                                  |
| api-spec `/api/curation/*` 6 EP                                                                    | 통과                | jobs(get)·jobs/{job_id}(get)·pairs/{id}(patch)·jobs/{job_id}/review(post)·image/{kind}(get)·crop/{row}(get)                                                |
| 계약 비대칭: 잡 상세 pair=top5有/job_id無, PATCH=job_id有/top5無                                   | 통과                | api-spec `CurationPair.job_id`="PATCH 응답에만", `.top5`="잡 상세에만". plan 타입이 정확히 미러                                                            |
| `supply` integer nullable, `top5.items{label,sim}`, status enum, PATCH 검증 400                    | 통과                | api-spec `CurationPair.supply`(integer,nullable), top5 items{label:string,sim:number}, `CurationPairPatch` "≥1 필수, 400 VALIDATION_ERROR"                 |
| `CurationImageKind = "original"                                                                    | "warped"`           | 통과                                                                                                                                                       | api-spec image `kind` enum=["original","warped"] |
| reviewJob ack = `{job_id,curation_reviewed}`                                                       | 통과(가정 아님)     | api-spec review 200 data 스키마 일치 → Risk #3 격하(L1)                                                                                                    |
| `autocomplete.tsx`에 blur/onCommit 부재, input·CommandItem 좌표                                    | 통과(좌표 미세오차) | `autocomplete.tsx`: input 60-73(plan "63-73"은 className 줄 기준, 요소 시작 60), CommandItem onSelect 88-94. onCommit 추가는 optional→기존 호출부 무영향 ✓ |
| `useItems` 반환 `{data:Item[]}`, `Item.item_name`/`id`                                             | 통과                | `use-items.ts:36` `{data,loading,error,refetch}`, `types/item.ts` `item_name:string; id?:number`                                                           |
| `use-debounce`/`empty-state`/`skeleton`/`pagination`/`PageContainer`/`PageHeader` 존재             | 통과                | 파일·`layout/index.ts` export 확인                                                                                                                         |
| 프론트 커버리지 fail-under 게이트 부재                                                             | 통과                | `vite.config.ts` test.coverage에 threshold 없음. include=utils/hooks/stores만                                                                              |
| settings `export default SettingsPage`, `PageContainer/PageHeader` 사용                            | 통과                | `app/settings/page.tsx` 확인                                                                                                                               |
| playwright `VITE_USE_MOCK=true ... --port 5174`, baseURL :5174, 1280x800                           | 통과                | `playwright.config.ts` 일치                                                                                                                                |
| **Task 5 단위 테스트 텍스트 일치**                                                                 | **불일치(H1)**      | `getByText("미검수")` ≠ 렌더 `"● 미검수"`                                                                                                                  |
| **Task 6 단위 테스트 헤딩 단일성**                                                                 | **불일치(H2)**      | PageHeader+본문 h1 중복으로 `getByText(/잡 #128/)` 2매칭                                                                                                   |

---

## task 분해 평가

- **의존 순서**: Task1 타입 → Task2 API(타입 소비) → Task3/4 훅(API 소비) → Task5 큐(훅3) →
  Task6 잡(훅4+utils+autocomplete) → Task7 라우팅(페이지) → Task8 e2e. 위상 정렬 정확, 각 task
  독립 커밋 가능. ✓
- **TDD RED 명세**: 각 task에 "실패 테스트 작성 → 실패 확인(에러 메시지까지)" 존재. 구체적. ✓
  단, Task5/6의 RED는 H1/H2 때문에 GREEN 진입 시 실패가 "구현 누락"이 아니라 "테스트 오타"로
  나타나 TDD 신호를 흐린다 — 수정 후 진행.
- **검증 명령 실재성**: `npm run test/lint/format:check/build/test:e2e` 모두 `frontend/AGENTS.md`·
  `package.json` 게이트와 일치. ✓
- **범위 적정성**: 순수 프론트, 백엔드 미변경, 8 task·신규 클래스 0. 복잡도 게이트(8파일/2클래스)
  미저촉. §6 비범위(유사 라벨 힌트·서버 상태 필터·라이브 관통 e2e·일괄 병합·재학습·모바일 분기)
  명확히 차단. ✓

## 불변식 보존 검증

- 옵티미스틱 PATCH 실패 롤백 + top5 보존 spread merge: 타입·mock·훅 3층 일치(Task1/2/4). merge에서
  `job_id` 제거·`top5` 미포함→기존 보존 로직 정확. 단 동시성 경쟁(M2)은 별도.
- invoice 절대 미변경: 큐레이션 파일만 신규/수정, invoice 경로 무접촉. ✓
- 이미지 placeholder degrade: crop/단계 이미지 `onError`→inline SVG placeholder, warp_ok=false→
  "워프 산출 없음". ✓
- 메인 nav 미포함: TopNav/BottomNav 무변경, 진입점 `/curation` URL + 설정 하단 데스크톱 링크. ✓

## 테스트 전략(spec §5) 반영

- vitest 단위(훅·URL빌더·변경강조) / playwright mock e2e / 라이브 관통 분리 — plan이 충실히 반영. ✓
- **mock 모드 ocrAPI real 고정 제약의 영향**: 확인함 — `ocrAPI=_realOcrAPI`(proxy 아님). 따라서
  업로드→confirm→큐레이션 라이브 관통은 mock e2e로 불가, plan은 이를 §5/Task8에서 정확히 인지하고
  큐레이션 mock 플로우로 한정 + 라이브는 별도 runbook으로 분리. curationAPI는 proxy라 mock 시드로
  e2e 가능 — 설계 정합. ✓

## plan-writer 리스크 5건 판정

1. URL 빌더 standalone 명명 → **수용(plan이 옳음)**. spec 객체-메서드 형태는 mock proxy에서 실제
   버그. 조치: spec을 갱신해 SSOT 정렬(L2).
2. Autocomplete `onCommit` prop 추가 vs native 대체 → **공용 컴포넌트 onCommit 추가 채택 타당**.
   optional이라 기존 호출부 무영향 검증됨. 단 **이중 발화(M1)** 가드 필수.
3. reviewJob ack 형태 가정 → **이미 api-spec 확정**, 가정 아님(L1). 격하.
4. 프론트 커버리지 게이트 부재 + 페이지 렌더 스모크 한정 → **사실 확인**. 페이지는 e2e가 정본.
   수용. 단 페이지 스모크 테스트 자체가 H1/H2로 깨지므로 먼저 고칠 것.
5. e2e 행 인덱스 결합 → **mock e2e 한정 허용**(L3). 시드·셀렉터 동기 주석 권장.

---

## 권고 처리 순서(구현 진입 전)

1. H1: Task5 단위 테스트 문자열을 `"● 미검수"`/정규식으로 교정(e2e와 통일).
2. H2+M3: 큐/잡 페이지에서 PageHeader 중복 제거(본문 헤더 단일화), 또는 테스트를 가시 단일
   노드 쿼리로. 데스크톱 우선 폼팩터와도 정합.
3. M1: 라벨 commit 단일 경로화(선택 진행 중 blur-commit 억제).
4. M2: 옵티미스틱 롤백을 필드 단위 부분 복원으로(또는 동시성 저빈도 명시 후 LOW 수용).
5. L2: spec §3.2를 standalone URL 빌더로 갱신(드리프트 차단).

이 5건 처리 시 Go. 구조·계약·분해는 그대로 진행 가능.
