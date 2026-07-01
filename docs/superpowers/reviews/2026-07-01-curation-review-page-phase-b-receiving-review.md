# Phase B 큐레이션 검수 페이지 — receiving-code-review (Step 3)

리뷰 대상 plan: `docs/superpowers/plans/2026-07-01-curation-review-page-phase-b.md`
검증 대상 리포트: `docs/superpowers/reviews/2026-07-01-curation-review-page-phase-b-eng-review.md`
정본 spec: `docs/superpowers/specs/2026-07-01-curation-review-page-phase-b-design.md`
방식: eng-review 각 지적을 실제 소스/`api-spec.json`/mock 시드로 직접 재검증 → ACCEPT/PARTIAL/REJECT + 실행 가능 edit 산출.

## 종합

eng-review의 종합판정 **Go-with-fixes**는 타당하다. H1·H2·M1·M3·L1은 코드 근거로 **ACCEPT**, M2는 down-scope하여 **PARTIAL**(최소 per-pair 롤백만 채택, 직렬화는 YAGNI로 REJECT), L2는 **ACCEPT(plan이 옳음 → spec을 고침)**, L3는 **ACCEPT(주석)**, L4는 **REJECT(별건·범위 밖)**. 맹목 수용·거부 없음.

CRITICAL 없음. 외부 계약 불변식(엔드포인트 6종·envelope·비대칭)은 plan에서 정확히 보존됨(별도 검증 통과).

## 판정표

| #   | 지적                                                                   | 판정                  | 근거(파일:라인)                                                                                                                                                                                                                         | 처리                                                                                                                  |
| --- | ---------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| H1  | Task5 테스트 `getByText("미검수")` vs 렌더 `"● 미검수"` 불일치         | **ACCEPT**            | plan L940 vs L1040. testing-library string 인자는 정규화 후 완전일치(exact 기본) → `"● 미검수"`≠`"미검수"` throw. e2e L1653은 `"● 미검수"`                                                                                              | 단위 테스트를 `getByText("● 미검수")`로 교정(e2e와 SSOT 통일)                                                         |
| H2  | Task6 `getByText(/잡 #128/)` 2매칭 throw                               | **ACCEPT**            | `PageHeader.tsx:36-58` `<header className="z-20 lg:hidden">` 안 `<h1>{title}</h1>`. jsdom CSS 미적용 → PageHeader h1 + 본문 h1(L1452) 둘 다 존재. `getNodeText`는 직속 텍스트노드만 → 본문 h1의 `<span>`(L1455) 제외, 둘 다 `"잡 #128"` | **M3 edit으로 동시 해소**(PageHeader 제거 → 본문 h1 단일). 테스트는 그대로 둠                                         |
| M1  | 라벨 선택 시 onCommit 이중 발화(stale→PATCH 2회)                       | **ACCEPT**            | `autocomplete.tsx` 현재 onBlur/onCommit 부재(60-73, 88-94). plan이 추가하는 onBlur(L1201)+onSelect(L1213) 둘 다 commit. 팝오버 클릭: mousedown→blur(타이핑 stale값)→click→onSelect(label). e2e(Task8)는 타이핑+blur만 → 미검출 latent   | autocomplete에 `selectingRef` 가드 추가(mousedown에서 set, blur에서 skip). 기존 호출부 무영향(onCommit optional)      |
| M2  | 옵티미스틱 동시 PATCH 롤백 lost-update                                 | **PARTIAL**           | plan L818 `snapshot=prev`(whole-job) + L842 `setJob(snapshot)`. B성공 커밋 후 A실패 롤백 시 B 클로버. 단 서버 상태 정확·로컬 UI만 stale·refetch 복구·저빈도 admin                                                                       | 최소 per-pair 스냅샷/롤백만 채택(cross-pair 보호). in-flight 직렬화는 YAGNI로 REJECT. same-pair 동시성은 범위 밖 명시 |
| M3  | PageHeader `lg:hidden`(모바일 전용) → 데스크톱 죽은 노드 + 모바일 중복 | **ACCEPT**            | `PageHeader.tsx:38` `z-20 lg:hidden`. spec §1 "데스크톱 우선". plan 큐/잡 본문 헤더는 `hidden lg:flex` 아님(L989, L1451) → 상시표시라 모바일 중복                                                                                       | 큐/잡 페이지에서 PageHeader 제거(본문 헤더 단일). H2 동시 해소                                                        |
| L1  | Risk #3 reviewJob ack을 "가정"으로 표기                                | **ACCEPT**            | api-spec review 200 `data:{job_id:integer, curation_reviewed:boolean}` 확정(검증함). plan mock(L483)·타입 일치                                                                                                                          | plan Self-Review 리스크 #3(L1750) "가정"→"확정" 격하. 코드 무변경                                                     |
| L2  | URL 빌더 standalone 명명 vs spec §3.2 `curationAPI.imageUrl`           | **ACCEPT(plan 옳음)** | plan L44 결정 vs spec §3.2 L99. `createMockProxy`가 객체 전체 wrap → proxied URL 빌더는 string 아닌 async wrapper 반환(검증함). plan의 standalone이 정확                                                                                | **plan 불변. spec §3.2를 standalone로 갱신**(외부계약 아닌 프론트 내부 명명이라 변경 가능)                            |
| L3  | e2e가 mock 시드 row_index 1 결합                                       | **ACCEPT**            | mock 시드 #128 pairs row_index 0·1(plan L327-357), e2e `getByLabel("행 1 라벨")`(L1661)·CurationPairRow ariaLabel `행 ${row_index} 라벨`(L1380). 결합 실재                                                                              | Task2 시드/Task8 셀렉터에 동기 주석 1줄. mock 한정 허용                                                               |
| L4  | `getApiBaseUrl()` 기본 :8000 vs 런타임 :8400                           | **REJECT(범위 밖)**   | `services/api.ts:22-30` 기존 동작. plan 책임 아님. URL빌더 테스트는 endsWith/substring라 통과                                                                                                                                           | plan 무변경. 기존 별건 이슈로 분리 추적(여기서 고치지 않음)                                                           |

## 확정 plan 수정안 (4단계 plan-editor용 실행 edit)

### EDIT-1 (H1) — Task 5 Step 1 단위 테스트 문자열

plan 파일 **L940**:

- 기존: `    expect(screen.getByText("미검수")).toBeInTheDocument();`
- 변경: `    expect(screen.getByText("● 미검수")).toBeInTheDocument();`

근거: 렌더(L1040)·e2e(L1653)와 문자열 SSOT 통일. (정규식 `/미검수/`도 가능하나 e2e가 `"● 미검수"` literal이라 literal로 통일하는 편이 드리프트 적음.)

### EDIT-2 (M3+H2) — 큐/잡 페이지에서 PageHeader 제거

Task 5 (`CurationQueuePage`):

- **L964** import에서 `PageHeader` 제거 → `import { PageContainer } from "@/components/layout";`
- **L987** `<PageHeader title="OCR 학습 큐레이션" showBack={false} />` 줄 삭제. (본문 헤더 L990가 상시 헤더 역할 — 변경 없음.)

Task 6 (`CurationJobPage`, Step 9):

- **L1405** import에서 `PageHeader` 제거 → `import { PageContainer } from "@/components/layout";`
- **L1449** `<PageHeader title={\`잡 #${job.job_id}\`} />` 줄 삭제. (본문 h1 L1452-1459 단일 유지.)

근거: PageHeader는 `lg:hidden`(모바일 전용)인데 본 기능은 데스크톱 우선이라 데스크톱 죽은 노드, 모바일에선 상시표시 본문 헤더와 중복. 제거 시 jsdom 2-h1 문제(H2)도 동시 해소 → Task6 테스트(L1283 `getByText(/잡 #128/)`)는 **변경 불필요**(본문 h1 단일 매칭).
참고: 모바일에서 잡 페이지 back 버튼이 사라지지만 데스크톱 우선 admin 도구의 허용 degrade. 본문 헤더에 back을 추가하지는 않는다(범위 최소화).

### EDIT-3 (M1) — Autocomplete 이중 발화 가드

Task 6 Step 5 (`autocomplete.tsx` 수정 명세)에 다음을 추가하도록 plan 본문을 보강:

1. 컴포넌트 본문(현 `autocomplete.tsx:45` `useState` 근처)에 ref 추가:
   ```tsx
   const selectingRef = React.useRef(false);
   ```
2. input `onBlur`(plan L1201)을 가드 형태로:
   ```tsx
   onBlur={() => {
     if (selectingRef.current) {
       selectingRef.current = false;
       return;
     }
     onCommit?.(inputValue);
   }}
   ```
3. 제안 항목(`CommandItem`)에 mousedown 플래그 추가(blur보다 먼저 실행됨):
   ```tsx
   onMouseDown={() => {
     selectingRef.current = true;
   }}
   ```
   기존 `onSelect`(plan L1210-1215) 끝에 `onCommit?.(s.label);`는 그대로 유지.

근거: 이벤트 순서 mousedown→blur→click(onSelect)에서 mousedown이 blur보다 먼저 플래그를 세워 blur의 stale commit을 억제. preventDefault를 쓰지 않으므로 기존 invoice-form 등 호출부의 포커스/선택 동작 불변(onCommit 미전달이라 onBlur 분기는 어차피 no-op). 타이핑 후 실제 blur는 정상 1회 commit(e2e Task8 경로 유지). spec §3.3 "blur 시 옵티미스틱 PATCH"와 정합.

### EDIT-4 (M2) — per-pair 롤백으로 down-scope

Task 4 Step 3 `patchPair`(plan L814-847)의 스냅샷/롤백을 whole-job → 해당 pair 한정으로 교체:

- L816 `let snapshot: CurationJobDetail | null = null;` → `let prevPair: CurationJobPair | undefined;`
- 옵티미스틱 블록(L818-825)을 다음으로:
  ```tsx
  setJob((prev) => {
    if (!prev) return prev;
    prevPair = prev.pairs.find((p) => p.id === id);
    return {
      ...prev,
      pairs: prev.pairs.map((p) => (p.id === id ? { ...p, ...patch } : p)),
    };
  });
  ```
- catch 롤백(L840-844)을 다음으로:
  ```tsx
  } catch {
    // 실패한 pair만 직전 값으로 복원(동시 발행된 다른 pair 변경은 보존).
    if (prevPair) {
      const restored = prevPair;
      setJob((prev) =>
        prev
          ? {
              ...prev,
              pairs: prev.pairs.map((p) => (p.id === id ? restored : p)),
            }
          : prev,
      );
    }
    toast.error("저장에 실패했습니다");
  }
  ```
- import에 `CurationJobPair` 타입 추가(L776-779 블록).

또한 Task 4 Step 1 테스트(L736-747 롤백 케이스)는 단일 PATCH라 그대로 통과. 가능하면 **cross-pair 보존 회귀 테스트 1건 추가**(선택): 서로 다른 pair에 옵티미스틱 적용 후 한쪽 실패 시 다른 쪽 변경이 유지되는지. (시드는 pair 2개 필요 → `jobDetail()` 픽스처에 pair 1개 추가하거나 별도 픽스처.)

근거: cross-pair lost-update(현실 시나리오: 행1 라벨 + 행2 제외 동시 in-flight, 행1 실패)를 최소 코드로 차단. in-flight 직렬화/큐는 저빈도 admin 도구에 과설계(YAGNI)라 도입하지 않음. same-pair 동시 발행(한 행에 라벨+제외 거의 동시)은 잔존하나 발생 가능성 극저·서버 상태 정확·refetch 복구 → 범위 밖으로 명시.
plan Self-Review "해소하지 못한 리스크"에 1줄 추가 권장: "동시성: per-pair 롤백으로 cross-pair는 보호. same-pair 동시 발행은 미보호(저빈도, refetch 복구)."

### EDIT-5 (L1) — Risk #3 문구 격하

plan **L1750** Self-Review 리스크 #3 문구를 가정→확정으로:

- 기존: "mock `reviewJob`은 `{job_id, curation_reviewed}` ack를 돌려준다고 가정(api-spec summary 기반). 실서버 ack 필드가 다르면 ..."
- 변경: "검수완료 ack `{job_id, curation_reviewed}`는 **api-spec review 200 data 스키마로 확정**(가정 아님). 소비처는 boolean만 사용."

근거: api-spec `/api/curation/jobs/{job_id}/review` 200 `data:{job_id:integer, curation_reviewed:boolean}` 직접 확인. 코드 변경 없음.

### EDIT-6 (L3) — e2e 시드 동기 주석

plan Task 8 Step 1 e2e(L1661 `getByLabel("행 1 라벨")`) 위에 주석 1줄:

```ts
// mock 시드 #128은 row_index 0·1 두 pair를 가진다(mocks/curation.ts). 시드 변경 시 이 셀렉터도 동반 수정.
```

그리고 Task 2 mock seed(plan L319 `mockCurationJobDetails` 위)에 대응 주석:

```ts
// 주의: e2e(curation.spec.ts)가 #128의 row_index 0·1 존재에 의존한다. 행 추가/삭제 시 e2e 셀렉터 동기.
```

근거: 시드↔셀렉터 결합 명시화로 후속 드리프트 방지. mock 한정이라 구조 변경 불필요.

## spec 수정안 (별도 — Phase A 외부 계약 불변, §3.2는 프론트 내부 명명)

### SPEC-EDIT-1 (L2) — §3.2 URL 빌더를 standalone로

`docs/.../specs/2026-07-01-curation-review-page-phase-b-design.md` §3.2:

- **L99-101**의 `imageUrl(jobId, kind)` / `cropUrl(jobId, row)` 표기를 다음으로 명확화:
  ```
  - `curationImageUrl(jobId, kind)` / `curationCropUrl(jobId, row)`
    → **순수 URL 문자열 빌더(standalone export, real-only)**. `curationAPI` 객체에 두지 않는다 —
    `createMockProxy`가 객체 전체를 감싸 mock 모드에서 string이 아닌 async wrapper를 반환하기 때문.
    `getApiBaseUrl()` 기반 경로 조립, `<img src>`에 직결.
  ```
- **L102** "mock proxy: JSON 4종 ... URL 빌더는 real만" 문장은 유지(정합).

근거: plan의 standalone 결정이 기술적으로 옳음(mock proxy 오염 회피, eng-review 코드표본 검증 통과). 외부 계약(엔드포인트 6종·응답 형태)은 불변이고 §3.2의 메서드 명명은 프론트 내부라 SSoT를 plan에 맞춰 갱신. 미갱신 시 향후 spec↔코드 드리프트.

## 적용 순서 권고

1. EDIT-1(H1), EDIT-2(M3+H2) — 테스트 RED→GREEN 정합 회복(구현 진입 전제).
2. EDIT-3(M1) — autocomplete 가드.
3. EDIT-4(M2) — per-pair 롤백 + 선택적 회귀 테스트.
4. EDIT-5/6(L1/L3), SPEC-EDIT-1(L2) — 문서·주석.
5. L4 — 본 plan에서 처리 안 함(별건 추적).

이 edit 적용 시 plan의 모든 "Expected: PASS"가 실제와 정합하며 Go.
