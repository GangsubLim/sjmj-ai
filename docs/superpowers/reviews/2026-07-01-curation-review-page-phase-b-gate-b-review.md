# Gate B (범용 리뷰) — OCR 큐레이션 검수 페이지 Phase B

- **날짜**: 2026-07-01
- **대상 PR**: #13 (`feat/curation-review-page-phase-b` → `devel`)
- **범위**: OCR 큐레이션 검수 페이지 Phase B — 순수 프론트엔드(`apps/invoice-ocr/frontend/`)
- **게이트**: 표준 구현 파이프라인 7단계 게이트 B(범용 리뷰) — 독립 러너 소유
- **리뷰 방법**: `code-review:code-review` 5-에이전트 팬아웃(CLAUDE.md 준수 / 버그 얕은 스캔 / 계약·상태관리 심층 / 과거 패턴 정합 / 주석·접근성) + 이슈별 0–100 스코어링
- **판정 방법론**: `superpowers:receiving-code-review` (findings = 평가 대상 신호, 맹목 수용 금지)

## 1. 스코어링 집계

- **≥80 (PR 코멘트 대상): 0건.** 최고 스코어 finding(F9, a11y)이 78 — 80 미만이라 PR 리뷰 코멘트는 남기지 않음(skill 필터 규약). 내부 판정·수정은 아래대로 수행.
- **50–79 밴드: 3건** — F9(78), F10(60), F5(55).
- **<50: 7건** — 오탐/의도된 설계/이미-보류/nitpick.

## 2. 전체 Findings (스코어 + 판정)

| ID    | 파일:위치                                    | severity | score | 판정             | 근거 요약                                                                                                                                                           |
| ----- | -------------------------------------------- | -------- | ----- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F9    | `app/curation/page.tsx:73`                   | HIGH     | 78    | **ACCEPT**       | `<tr role="link">`가 자식 `<td>`(role cell)의 required parent(`row`)를 파괴 → axe `aria-required-parent`/`aria-allowed-role` 위반. 실재·검증됨(내부툴이라 80 미만)  |
| F10   | `app/curation/page.tsx:79`                   | MEDIUM   | 60    | **ACCEPT**       | `role="link"`인데 Space까지 활성화 처리(link는 Enter만). JS 네비게이션이라 button이 정확. F9 수정으로 함께 해소                                                     |
| F5    | `hooks/use-curation-job.ts:43`               | MEDIUM   | 55    | **REJECT(보류)** | `fetch`에 jobId staleness 가드 없음(자매 훅 `use-curation-jobs`와 비대칭). 그러나 job→job 직접 내비게이션이 UI에 없어 race 도달 불가(모든 경로가 remount). YAGNI    |
| F4/F6 | `hooks/use-curation-job.ts:59,94`            | HIGH/MED | 45    | **REJECT(보류)** | 같은-pair 동시 PATCH 롤백 오염. 게이트 A·eng-review·receiving-review에서 이미 '후속 과제 보류' 확정. cross-pair는 정상 보호 확인. 재지적 = 이미-처리                |
| F11   | `components/curation/CurationPairRow.tsx:87` | MEDIUM   | 30    | **REJECT**       | "학습 비대상" 라벨이 status 무관 항상 표시 → 설계 정본 `specs/…-design.md:170`("금액: supply 읽기 전용 + '학습 비대상' 배지")에 명시된 **의도**. 상태 배지 아님     |
| F7    | `hooks/use-curation-job.ts:62`               | LOW      | 35    | **REJECT**       | `if (!prevPair) return` 무피드백 no-op. 호출부가 항상 로드된 pairs만 순회 → 도달 불가 방어경로. 삼킴 아님(옵티미스틱 실행 자체가 안 됨)                             |
| F1    | `hooks/use-curation-jobs.ts:37`              | LOW      | 40    | **REJECT**       | 에러 메시지 추출 인라인(자매 훅은 `errorMessage` 헬퍼). 1-liner 2회용 공유 추출은 YAGNI. 기능 무영향                                                                |
| F2    | `app/curation/page.tsx:26`                   | LOW      | 30    | **REJECT**       | `useCurationJobs(20)`이 기본값 20을 재하드코딩. 호출부 의도 명시로 볼 수 있는 nitpick                                                                               |
| F3    | `components/curation/CurationPairRow.tsx:45` | LOW      | 15    | **REJECT**       | onPatch floating promise 지적 = **오탐**. prop 타입이 `(id, patch) => void`라 타입상 floating 아님. eslint에 `no-floating-promises`(type-checked) 미활성, lint 통과 |
| F8    | `lib/pagination.ts`                          | LOW      | 30    | **REJECT**       | 전용 단위 테스트 부재. 함수는 `list/page.tsx`에서 바이트 동일 이동(회귀 없음), 테스트 공백은 **사전존재**. 선택적 개선                                              |

**요약: ACCEPT 2(F9+F10, 단일 수정으로 해소) · REJECT 8**

## 3. 계약 불변식 검증 (리뷰어 #3 심층 + 교차 확인)

외부 계약 불변식 전부 보존 확인 — 위반 0:

- 성공 envelope `{success, data, pagination?}` — `types/api.ts`의 `ListResponse`/`SingleResponse` shape 준수.
- **계약 비대칭**: GET pair = top5 有·job_id 無 / PATCH = top5 無·job_id 有 — `types/curation.ts`가 `CurationPairBase`를 공통분모로 `CurationJobPair`(+top5)/`CurationPairPatchResult`(+job_id)로 타입 레벨 강제. real(`services/api.ts`)·mock(`mocks/api.ts`) 양쪽 일관. api-spec과 일치.
- 옵티미스틱 PATCH top5 보존 · per-pair 롤백 — `use-curation-job` 훅이 `{ job_id, ...base }` 구조분해로 top5 보존, jobRef 미러로 per-pair 스냅샷. cross-pair lost-update(receiving-review M2)는 해소 확인.
- invoice 절대 미변경 — `invoice_id`는 읽기 표시 전용, curation 훅/서비스 어디서도 invoice API 미호출.
- api-spec이 타입 SSoT — a11y 수정은 계약 스키마 불변.

## 4. 적용한 수정 (ACCEPT — 별도 `fix:` 커밋)

수정자는 리뷰어와 분리된 **편집 권한 서브에이전트**가 수행(receiving 방법론, 자기 합리화 차단).

- **F9 + F10 (a11y, `app/curation/page.tsx`)**: 검수 큐 테이블 행의 `<tr role="link" tabIndex onKeyDown>`을 시맨틱 `<tr onClick>`(role 미지정=암묵 `row`)으로 되돌리고, 키보드·SR 진입점을 첫 `<td>` 내부 **네이티브 `<button type="button" aria-label="잡 #{id} 상세">`** 로 이동. 마우스 행-전체-클릭은 `<tr onClick>` 유지, 버튼 클릭은 `e.stopPropagation()`으로 이중 발화 방지.
  - 결과: `<td>` cell이 required parent `row`를 회복(axe 위반 해소), 버튼이 Enter+Space를 네이티브로 처리(F10 role/키보드 불일치 해소), aria-label 보존.
  - 프로젝트 eslint엔 jsx-a11y 플러그인이 없어 `<tr onClick>`(role 없음)은 lint 무이슈.
- **테스트 동반 수정**:
  - `app/curation/page.test.tsx`: `setupSingleJob()` → `getByRole("button", { name: "잡 #128 상세" })`. Enter-keyDown 테스트(네이티브 button은 jsdom에서 keyDown→click 미합성)를 접근성 회귀 가드로 교체 — `getByRole("row", { name: /128/ })`(헤더 행 충돌 회피용 name 필터) + `getByRole("button", { name: "잡 #128 상세" })` 존재 단언.
  - `tests/e2e/curation.spec.ts`: `getByRole("link", …)` → `getByRole("row").filter({ has: getByRole("button", { name: "잡 #128 상세" }) }).getByText("✓ 검수됨")` 및 주석 갱신.

## 5. 검증 재실행 (전부 PASS)

`apps/invoice-ocr/frontend`:

- `npm run lint` — PASS (0 errors)
- `npm run format:check` — PASS
- `npm run test` — PASS (32 파일, **189/189**)
- `npm run build` — PASS (tsc -b + vite build)
- `npm run test:e2e -- curation.spec.ts` — PASS (**2/2**; 이미지 ECONNREFUSED는 mock 모드의 의도된 placeholder degrade 경로)

## 6. 보류 항목(후속 과제 트래킹 권고)

- **F5**: `use-curation-job` fetch staleness 가드 — 현재 job→job 직접 내비게이션 경로가 없어 race 도달 불가(모든 경로가 컴포넌트 remount). job→job 이동이 도입되면 자매 훅(`use-curation-jobs`)의 reqId 가드 패턴을 미러링 권장.
- **F4/F6**: 같은-pair 동시 PATCH 롤백 오염 — 필드 단위 롤백 또는 pair별 in-flight 스냅샷 키잉. 옵티미스틱 동시성 설계 결정 필요(게이트 A와 동일 판정 유지).
- **F8**: `lib/pagination.ts` 전용 단위 테스트(순수 함수 직접 커버) — 선택적.
