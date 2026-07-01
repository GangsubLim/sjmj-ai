# OCR 큐레이션 검수 페이지 (Phase B) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase A가 확정한 `/api/curation/*` 6개 엔드포인트 위에 데스크톱 우선 OCR 학습쌍 검수 UI(큐 목록 + 잡 드릴다운)를 순수 프론트엔드로 올린다.

**Architecture:** `types/curation.ts`(api-spec 미러) → `services/api.ts`의 `curationAPI`(JSON 4종 mock proxy + 이미지 URL 빌더 real-only) → 페칭/옵티미스틱 훅 2종 → `CurationQueuePage`/`CurationJobPage` 2화면 → `main.tsx` lazy 라우트 + 설정 보조 링크 → playwright mock e2e. 메인 nav에는 넣지 않는다.

**Tech Stack:** React 19 + Vite + Tailwind v4 + shadcn, react-router-dom, axios(`services/api.ts` 단일 진입점), sonner(toast), vitest + @testing-library/react(단위), Playwright(e2e, `VITE_USE_MOCK=true`).

## Global Constraints

이 섹션의 불변식은 모든 task에 암묵적으로 포함된다. 위반 시 그 task는 실패다.

- **외부 계약 불변(Phase A 확정):** 엔드포인트 6종·응답 형태를 변경하지 않는다. 백엔드는 만지지 않는다.
- **계약 비대칭(절대 보존):** `GET /jobs/{id}`의 pair는 `top5` 포함·`job_id` 없음. `PATCH /pairs/{id}` 응답은 `top5` 없음·`job_id` 포함. 타입과 merge 로직이 이 비대칭을 그대로 반영해야 한다.
- **옵티미스틱 PATCH 불변식:** 토글/라벨 commit 시 로컬 `pairs`를 즉시 불변 갱신 → `patchPair` 발행 → 성공이면 응답을 기존 pair에 **spread merge**(응답에 없는 `top5`는 기존 값 보존) → 실패면 직전 스냅샷으로 롤백 + sonner 에러 토스트.
- **분리 불변식:** invoice(거래명세서) 데이터는 절대 건드리지 않는다. 큐레이션은 학습쌍만 수정한다.
- **이미지 mock 불가:** 이미지(`/image/{kind}`·`/crop/{row}`)는 raw FileResponse라 mock 부적합. URL 빌더는 real-only이며, mock 모드/404에서는 `onError` placeholder로 degrade한다.
- **메인 nav 미포함:** `TopNav`/`BottomNav`에 큐레이션 링크를 추가하지 않는다. 진입점은 `/curation` URL 직접 + 설정 페이지 하단 보조 링크 1개(데스크톱 한정)뿐.
- **api-spec이 타입 SSoT:** `types/curation.ts`는 `.claude/ai-context/api-spec.json`의 `CurationJobSummary`/`CurationPair`/`CurationJobDetail`/`CurationPairPatch` 스키마를 미러한다. 드리프트가 보이면 api-spec이 우선이다.
- **API 단일 진입점:** 모든 엔드포인트 호출은 `services/api.ts`에서만 정의한다. 컴포넌트/훅은 거기서 export된 것만 import한다.
- **단위 테스트 코로케이트:** `*.test.ts(x)`는 대상과 같은 폴더. e2e만 `tests/e2e/`.
- **포맷 게이트:** 작업 종료 전 `npm run lint`·`npm run format:check`·`npm run build`(tsc 포함)가 통과해야 한다. Prettier 기본값(double quote·세미콜론·2-space). 파일 끝 newline.

---

## 확정 좌표 (재grep 검증 완료)

- `src/main.tsx:24-66` — `createBrowserRouter` → `AppShell` children 배열. lazy + `<Suspense fallback={LazyFallback}>` 패턴(`CompanyManagePage` 등). 여기에 라우트 2개 추가.
- `src/services/api.ts` — `USE_MOCK`(18), `getApiBaseUrl()`(22-30, 기본 `http://localhost:8000/api`), `createMockProxy`(322-338), `getMock()`(317-320, `@/mocks/api` 로드), 슬라이스별 `export const xAPI = createMockProxy(...)`(340-373). `ocrAPI`는 real-only export(307) — URL 빌더 real-only의 선례.
- `src/types/api.ts` — `ListResponse<T> { data: T[]; pagination?: Pagination }`, `Pagination { page, limit, total, totalPages }`, `SingleResponse<T> { data: T; message? }`.
- `src/hooks/use-invoices.ts` — 페칭 훅 레퍼런스(useState+useCallback fetch+useEffect, 한국어 error, refetch). `src/hooks/use-items.ts` — itemSuggestions 페칭. `src/hooks/use-items.test.ts` — `vi.mock("@/services/api")` + `renderHook`/`waitFor`/`act` 패턴(vitest globals).
- `src/mocks/api.ts` — `export const mockXAPI = {...}` 인메모리 스토어(deep clone + `delay()`). `src/mocks/index.ts` — seed 데이터 모듈 re-export.
- `src/components/ui/autocomplete.tsx` — `Autocomplete`(value/onChange/suggestions/onAddNew) + `AutocompleteSuggestion {label,value,meta}`. `src/components/invoice/invoice-form.tsx:203,553` — `useItems(debouncedItemQuery)` → `AutocompleteSuggestion[]` → `<Autocomplete>` 와이어링 레퍼런스.
- `src/components/ui/pagination.tsx` — `Pagination`/`PaginationContent`/`PaginationItem`/`PaginationLink`/`PaginationPrevious`/`PaginationNext`. `src/app/list/page.tsx:621-671` — 사용 예. `src/components/ui/empty-state.tsx` — `EmptyState {icon,title,description,action?}`.
- `src/app/settings/page.tsx` — `export default SettingsPage`, `PageContainer`/`PageHeader`(`@/components/layout`). 하단 보조 링크 추가 위치.
- `playwright.config.ts` — `testDir: ./tests/e2e`, webServer `VITE_USE_MOCK=true npm run dev -- --port 5174`, baseURL `:5174`, desktop 1280x800. `tests/e2e/sales-performance.spec.ts` — 패턴 레퍼런스.
- `vite.config.ts:24-32` — vitest `globals: true`, `environment: "jsdom"`, `setupFiles: ["./src/test/setup.ts"]`, include `src/**/*.test.{ts,tsx}`. 프론트 커버리지 fail-under 게이트 없음(페이지는 e2e로 커버).
- api-spec 스키마 확인: `CurationPair.job_id`(PATCH 응답에만), `CurationPair.top5`(잡 상세에만), `supply` integer nullable, `top5.items {label:string, sim:number}`, `CurationPairPatch`(status|canonical_label 중 ≥1, 실패 400).

### 설계 결정 / 가정 (구현자 필독)

- **이미지 URL 빌더는 standalone export로 분리.** spec은 `curationAPI.imageUrl`/`cropUrl`로 표기하지만, `createMockProxy`가 객체 전체를 감싸 mock 모드에서 빈 mock 메서드로 떨어지는 것을 막기 위해 URL 빌더는 `curationImageUrl`/`curationCropUrl` standalone 함수로 export한다(real-only, `ocrAPI` real-only 선례와 동일 취지). `curationAPI`(proxied)에는 JSON 4종만 둔다. — **계약/동작 동일, 이름만 명시화.**
- **라벨 Combobox는 기존 `Autocomplete` 재사용 + optional `onCommit` prop 1개 추가.** `Autocomplete`에 blur·select 시 발화하는 `onCommit?: (value: string) => void`를 추가한다(기존 호출부는 prop 미전달 → 무영향). 라벨 편집은 이 `onCommit`에서 옵티미스틱 PATCH를 발행한다.
- **변경 강조 판정은 `utils/curation.ts` 순수 함수로 추출**해 단위 테스트한다(라디오 팝오버 렌더 테스트 회피).
- **페이지는 vitest 렌더 스모크(훅 mock) + e2e로 커버.** 상호작용(blur PATCH·토글·검수완료)은 Task 8 e2e가 정본 검증.

---

## Task 1: `types/curation.ts` — api-spec 타입 미러

**Files:**

- Create: `apps/invoice-ocr/frontend/src/types/curation.ts`
- Test: `apps/invoice-ocr/frontend/src/types/curation.test.ts`

**Interfaces:**

- Produces: `CurationTop5Item`, `CurationJobSummary`, `CurationPairBase`, `CurationJobPair`(`+top5`), `CurationPairPatchResult`(`+job_id`), `CurationJobDetail`, `CurationPairPatch`, `CurationImageKind`. 이후 모든 task가 이 이름·필드를 그대로 쓴다.

- [ ] **Step 1: 실패 테스트 작성**

타입 전용 파일이라 타입-레벨 가드 + 비대칭 보장 테스트를 쓴다.

```ts
// src/types/curation.test.ts
import { describe, it, expectTypeOf } from "vitest";
import type {
  CurationJobPair,
  CurationPairPatchResult,
  CurationJobDetail,
  CurationPairPatch,
} from "./curation";

describe("curation 타입 계약", () => {
  it("잡 상세 pair는 top5를 가지고 job_id는 없다", () => {
    expectTypeOf<CurationJobPair>().toHaveProperty("top5");
    expectTypeOf<CurationJobPair>().not.toHaveProperty("job_id");
  });

  it("PATCH 결과는 job_id를 가지고 top5는 없다", () => {
    expectTypeOf<CurationPairPatchResult>().toHaveProperty("job_id");
    expectTypeOf<CurationPairPatchResult>().not.toHaveProperty("top5");
  });

  it("잡 상세는 pairs 배열을 가진다", () => {
    expectTypeOf<CurationJobDetail["pairs"]>().toEqualTypeOf<
      CurationJobPair[]
    >();
  });

  it("PATCH 본문은 status·canonical_label 모두 선택적이다", () => {
    expectTypeOf<CurationPairPatch>().toEqualTypeOf<{
      status?: "included" | "excluded";
      canonical_label?: string;
    }>();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/types/curation.test.ts`
Expected: FAIL — `Cannot find module './curation'`.

- [ ] **Step 3: 최소 구현**

```ts
// src/types/curation.ts
// api-spec(.claude/ai-context/api-spec.json)의 Curation* 스키마를 미러한다.
// 드리프트 시 api-spec이 SSoT.

export interface CurationTop5Item {
  label: string;
  sim: number;
}

export interface CurationJobSummary {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  pair_count: number;
  unreviewed_count: number;
  created_at: string;
}

// 잡 상세 pair와 PATCH 결과가 공유하는 공통 필드.
export interface CurationPairBase {
  id: number;
  crop_ref: string;
  row_index: number;
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
  supply: number | null;
  status: "included" | "excluded";
  reviewed_at: string | null;
}

// GET /jobs/{id} 의 pair — top5 포함, job_id 없음.
export interface CurationJobPair extends CurationPairBase {
  top5: CurationTop5Item[];
}

// PATCH /pairs/{id} 응답 — job_id 포함, top5 없음(계약 비대칭).
export interface CurationPairPatchResult extends CurationPairBase {
  job_id: number;
}

export interface CurationJobDetail {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  warp_ok: boolean;
  created_at: string;
  pairs: CurationJobPair[];
}

export type CurationPairPatch = {
  status?: "included" | "excluded";
  canonical_label?: string;
};

export type CurationImageKind = "original" | "warped";
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/types/curation.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/types/curation.ts apps/invoice-ocr/frontend/src/types/curation.test.ts
git commit -m "feat(curation): types/curation.ts — api-spec 타입 미러(계약 비대칭 포함)"
```

---

## Task 2: `curationAPI` + URL 빌더 + mock

**Files:**

- Modify: `apps/invoice-ocr/frontend/src/services/api.ts`(import 추가 + `_realCurationAPI`·URL 빌더·`curationAPI` export)
- Create: `apps/invoice-ocr/frontend/src/mocks/curation.ts`(seed 데이터)
- Modify: `apps/invoice-ocr/frontend/src/mocks/api.ts`(`mockCurationAPI` export)
- Modify: `apps/invoice-ocr/frontend/src/mocks/index.ts`(seed re-export)
- Test: `apps/invoice-ocr/frontend/src/services/curation-api.test.ts`

**Interfaces:**

- Consumes: Task 1 타입, `getApiBaseUrl`(api.ts 내부), `createMockProxy`/`getMock`(api.ts 내부).
- Produces:
  - `curationAPI.getJobs(params?: { page?: number; limit?: number }): Promise<ListResponse<CurationJobSummary>>`
  - `curationAPI.getJob(jobId: number): Promise<SingleResponse<CurationJobDetail>>`
  - `curationAPI.patchPair(id: number, patch: CurationPairPatch): Promise<SingleResponse<CurationPairPatchResult>>`
  - `curationAPI.reviewJob(jobId: number): Promise<SingleResponse<{ job_id: number; curation_reviewed: boolean }>>`
  - `curationImageUrl(jobId: number, kind: CurationImageKind): string`(standalone, real-only)
  - `curationCropUrl(jobId: number, row: number): string`(standalone, real-only)
  - `mockCurationAPI`(mocks/api.ts) — 위 JSON 4종 mock 구현.

- [ ] **Step 1: 실패 테스트 작성 (URL 빌더 단위)**

```ts
// src/services/curation-api.test.ts
import { describe, it, expect } from "vitest";
import { curationImageUrl, curationCropUrl } from "./api";

describe("curation URL 빌더", () => {
  it("imageUrl은 잡/kind 경로를 조립한다", () => {
    const url = curationImageUrl(128, "warped");
    expect(url).toContain("/api/");
    expect(url.endsWith("/curation/jobs/128/image/warped")).toBe(true);
  });

  it("cropUrl은 잡/행 경로를 조립한다", () => {
    const url = curationCropUrl(128, 3);
    expect(url.endsWith("/curation/jobs/128/crop/3")).toBe(true);
  });

  it("original kind도 처리한다", () => {
    expect(
      curationImageUrl(5, "original").endsWith(
        "/curation/jobs/5/image/original",
      ),
    ).toBe(true);
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/services/curation-api.test.ts`
Expected: FAIL — `curationImageUrl` is not exported.

- [ ] **Step 3: 최소 구현 — `services/api.ts`**

`api.ts` 상단 import 블록에 추가:

```ts
import type {
  CurationJobSummary,
  CurationJobDetail,
  CurationPairPatch,
  CurationPairPatchResult,
  CurationImageKind,
} from "@/types/curation";
```

`ocrAPI` export(307줄) 바로 아래, mock proxy 블록(309줄 주석) 위에 추가:

```ts
// --- Real Curation API (검수 큐레이션) ---

const _realCurationAPI = {
  getJobs: async (params?: {
    page?: number;
    limit?: number;
  }): Promise<ListResponse<CurationJobSummary>> => {
    const response = await api.get("/curation/jobs", {
      params: { page: params?.page ?? 1, limit: params?.limit ?? 20 },
    });
    return response.data;
  },

  getJob: async (jobId: number): Promise<SingleResponse<CurationJobDetail>> => {
    const response = await api.get(`/curation/jobs/${jobId}`);
    return response.data;
  },

  patchPair: async (
    id: number,
    patch: CurationPairPatch,
  ): Promise<SingleResponse<CurationPairPatchResult>> => {
    const response = await api.patch(`/curation/pairs/${id}`, patch);
    return response.data;
  },

  reviewJob: async (
    jobId: number,
  ): Promise<
    SingleResponse<{ job_id: number; curation_reviewed: boolean }>
  > => {
    const response = await api.post(`/curation/jobs/${jobId}/review`);
    return response.data;
  },
};

// 이미지 URL 빌더 — axios 호출 아님, real-only(mock 부적합한 raw FileResponse).
// <img src>에 직결한다. getApiBaseUrl() 기반으로 경로만 조립.
export const curationImageUrl = (
  jobId: number,
  kind: CurationImageKind,
): string => `${getApiBaseUrl()}/curation/jobs/${jobId}/image/${kind}`;

export const curationCropUrl = (jobId: number, row: number): string =>
  `${getApiBaseUrl()}/curation/jobs/${jobId}/crop/${row}`;
```

mock proxy export 블록(`salesRecordAPI` 아래, `export default api;` 위)에 추가:

```ts
export const curationAPI = createMockProxy(
  _realCurationAPI,
  async () => (await getMock()).mockCurationAPI as typeof _realCurationAPI,
);
```

- [ ] **Step 4: URL 빌더 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/services/curation-api.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: mock seed + mock API 구현**

```ts
// src/mocks/curation.ts
import type { CurationJobDetail } from "@/types/curation";

// 인메모리 정본: 잡 상세 배열. 요약/목록은 여기서 파생한다.
// 주의: e2e(curation.spec.ts)가 #128의 row_index 0·1 존재에 의존한다. 행 추가/삭제 시 e2e 셀렉터 동기.
export const mockCurationJobDetails: CurationJobDetail[] = [
  {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    warp_ok: true,
    created_at: "2026-06-30T09:10:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "배추",
        final_label: "배추",
        canonical_label: "배추",
        supply: 12000,
        status: "included",
        reviewed_at: null,
        top5: [
          { label: "배추", sim: 0.91 },
          { label: "무", sim: 0.42 },
          { label: "파", sim: 0.31 },
        ],
      },
      {
        id: 9002,
        crop_ref: "128/1",
        row_index: 1,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        reviewed_at: null,
        top5: [
          { label: "무", sim: 0.77 },
          { label: "배추", sim: 0.21 },
        ],
      },
    ],
  },
  {
    job_id: 127,
    invoice_id: 340,
    curation_reviewed: true,
    warp_ok: false,
    created_at: "2026-06-30T08:00:00",
    pairs: [
      {
        id: 8001,
        crop_ref: "127/0",
        row_index: 0,
        draft_label: "당근",
        final_label: "당근",
        canonical_label: "당근",
        supply: 5000,
        status: "included",
        reviewed_at: "2026-06-30T08:30:00",
        top5: [{ label: "당근", sim: 0.88 }],
      },
    ],
  },
];
```

`mocks/index.ts` 끝에 추가:

```ts
export { mockCurationJobDetails } from "./curation";
```

`mocks/api.ts` import 블록에 추가:

```ts
import type {
  CurationJobSummary,
  CurationJobDetail,
  CurationPairPatch,
  CurationPairPatchResult,
} from "@/types/curation";
import { mockCurationJobDetails } from "./curation";
```

`mocks/api.ts` 끝(파일 마지막 export 뒤)에 추가:

```ts
// --- Curation API (검수 큐레이션) ---

let curationJobs: CurationJobDetail[] = JSON.parse(
  JSON.stringify(mockCurationJobDetails),
);

const toSummary = (job: CurationJobDetail): CurationJobSummary => ({
  job_id: job.job_id,
  invoice_id: job.invoice_id,
  curation_reviewed: job.curation_reviewed,
  pair_count: job.pairs.length,
  unreviewed_count: job.pairs.filter((p) => p.reviewed_at === null).length,
  created_at: job.created_at,
});

export const mockCurationAPI = {
  getJobs: async (params?: { page?: number; limit?: number }) => {
    await delay();
    // 서버 정렬 갈음: 미검수(false) 우선, 그다음 최신 생성순.
    const sorted = [...curationJobs].sort((a, b) => {
      if (a.curation_reviewed !== b.curation_reviewed) {
        return a.curation_reviewed ? 1 : -1;
      }
      return b.created_at.localeCompare(a.created_at);
    });
    const page = params?.page ?? 1;
    const limit = params?.limit ?? 20;
    const total = sorted.length;
    const start = (page - 1) * limit;
    return {
      data: sorted.slice(start, start + limit).map(toSummary),
      pagination: { page, limit, total, totalPages: Math.ceil(total / limit) },
    };
  },

  getJob: async (jobId: number) => {
    await delay();
    const found = curationJobs.find((j) => j.job_id === jobId);
    if (!found) throw new Error("잡을 찾을 수 없습니다");
    return { data: JSON.parse(JSON.stringify(found)) as CurationJobDetail };
  },

  patchPair: async (id: number, patch: CurationPairPatch) => {
    await delay();
    let result: CurationPairPatchResult | null = null;
    curationJobs = curationJobs.map((job) => ({
      ...job,
      pairs: job.pairs.map((p) => {
        if (p.id !== id) return p;
        const updated = {
          ...p,
          ...patch,
          reviewed_at: p.reviewed_at ?? new Date().toISOString(),
        };
        // PATCH 응답 형태: job_id 포함, top5 제외(계약 비대칭).
        const { top5: _top5, ...base } = updated;
        result = { ...base, job_id: job.job_id };
        return updated;
      }),
    }));
    if (!result) throw new Error("쌍을 찾을 수 없습니다");
    return { data: result as CurationPairPatchResult };
  },

  reviewJob: async (jobId: number) => {
    await delay();
    curationJobs = curationJobs.map((job) =>
      job.job_id === jobId
        ? {
            ...job,
            curation_reviewed: true,
            pairs: job.pairs.map((p) => ({
              ...p,
              reviewed_at: p.reviewed_at ?? new Date().toISOString(),
            })),
          }
        : job,
    );
    return { data: { job_id: jobId, curation_reviewed: true } };
  },
};
```

- [ ] **Step 6: 전체 단위 + 빌드 검증**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/services/curation-api.test.ts && npm run lint && npm run build`
Expected: 테스트 PASS, lint 0 error, `tsc -b && vite build` 성공(mock proxy 타입 정합).

- [ ] **Step 7: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/services/api.ts apps/invoice-ocr/frontend/src/services/curation-api.test.ts apps/invoice-ocr/frontend/src/mocks/curation.ts apps/invoice-ocr/frontend/src/mocks/api.ts apps/invoice-ocr/frontend/src/mocks/index.ts
git commit -m "feat(curation): curationAPI(JSON 4종 mock proxy) + 이미지 URL 빌더 real-only + mock seed"
```

---

## Task 3: `use-curation-jobs` 훅 — 큐 목록 + 페이지네이션

**Files:**

- Create: `apps/invoice-ocr/frontend/src/hooks/use-curation-jobs.ts`
- Test: `apps/invoice-ocr/frontend/src/hooks/use-curation-jobs.test.ts`

**Interfaces:**

- Consumes: `curationAPI.getJobs`, `CurationJobSummary`, `ListResponse`.
- Produces: `useCurationJobs(limit?: number): { data: CurationJobSummary[]; total: number; page: number; totalPages: number; loading: boolean; error: string | null; setPage: (p: number) => void; refetch: () => void }`.

- [ ] **Step 1: 실패 테스트 작성**

```ts
// src/hooks/use-curation-jobs.test.ts
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useCurationJobs } from "./use-curation-jobs";
import { curationAPI } from "@/services/api";
import type { CurationJobSummary } from "@/types/curation";

vi.mock("@/services/api", () => ({
  curationAPI: { getJobs: vi.fn() },
}));

const mockGetJobs = vi.mocked(curationAPI.getJobs);

function summary(over: Partial<CurationJobSummary> = {}): CurationJobSummary {
  return {
    job_id: 1,
    invoice_id: 10,
    curation_reviewed: false,
    pair_count: 3,
    unreviewed_count: 3,
    created_at: "2026-06-30T09:00:00",
    ...over,
  };
}

function listResponse(data: CurationJobSummary[], total = data.length) {
  return { data, pagination: { page: 1, limit: 20, total, totalPages: 1 } };
}

describe("useCurationJobs", () => {
  beforeEach(() => vi.clearAllMocks());

  it("잡 목록과 total을 노출한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([summary({ job_id: 128 })], 42));
    const { result } = renderHook(() => useCurationJobs());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.total).toBe(42);
  });

  it("setPage가 page 파라미터로 재조회한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));
    const { result } = renderHook(() => useCurationJobs(20));
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 1, limit: 20 }),
    );
    act(() => result.current.setPage(2));
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 2, limit: 20 }),
    );
  });

  it("에러 메시지를 한국어로 노출한다", async () => {
    mockGetJobs.mockRejectedValue(new Error("조회 실패"));
    const { result } = renderHook(() => useCurationJobs());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("조회 실패");
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/hooks/use-curation-jobs.test.ts`
Expected: FAIL — `Cannot find module './use-curation-jobs'`.

- [ ] **Step 3: 최소 구현**

```ts
// src/hooks/use-curation-jobs.ts
import { useCallback, useEffect, useState } from "react";
import type { CurationJobSummary } from "@/types/curation";
import { curationAPI } from "@/services/api";

interface UseCurationJobsReturn {
  data: CurationJobSummary[];
  total: number;
  page: number;
  totalPages: number;
  loading: boolean;
  error: string | null;
  setPage: (p: number) => void;
  refetch: () => void;
}

export function useCurationJobs(limit = 20): UseCurationJobsReturn {
  const [data, setData] = useState<CurationJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJobs({ page, limit });
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
      setTotalPages(res.pagination?.totalPages ?? 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "검수 큐를 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, [page, limit]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return {
    data,
    total,
    page,
    totalPages,
    loading,
    error,
    setPage,
    refetch: fetch,
  };
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/hooks/use-curation-jobs.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/hooks/use-curation-jobs.ts apps/invoice-ocr/frontend/src/hooks/use-curation-jobs.test.ts
git commit -m "feat(curation): use-curation-jobs 훅 — 큐 목록 페칭 + 페이지네이션"
```

---

## Task 4: `use-curation-job` 훅 — 잡 상세 + 옵티미스틱 PATCH + reviewJob

**Files:**

- Create: `apps/invoice-ocr/frontend/src/hooks/use-curation-job.ts`
- Test: `apps/invoice-ocr/frontend/src/hooks/use-curation-job.test.ts`

**Interfaces:**

- Consumes: `curationAPI.getJob`/`patchPair`/`reviewJob`, `toast`(sonner), `CurationJobDetail`/`CurationJobPair`/`CurationPairPatch`/`CurationPairPatchResult`.
- Produces: `useCurationJob(jobId: number | undefined): { job: CurationJobDetail | null; loading: boolean; error: string | null; patchPair: (id: number, patch: CurationPairPatch) => Promise<void>; reviewJob: () => Promise<boolean>; refetch: () => void }`.

- [ ] **Step 1: 실패 테스트 작성 (옵티미스틱 성공 merge·롤백·top5 보존)**

```ts
// src/hooks/use-curation-job.test.ts
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useCurationJob } from "./use-curation-job";
import { curationAPI } from "@/services/api";
import type {
  CurationJobDetail,
  CurationPairPatchResult,
} from "@/types/curation";

vi.mock("@/services/api", () => ({
  curationAPI: { getJob: vi.fn(), patchPair: vi.fn(), reviewJob: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const mockGetJob = vi.mocked(curationAPI.getJob);
const mockPatchPair = vi.mocked(curationAPI.patchPair);
const mockReviewJob = vi.mocked(curationAPI.reviewJob);

function jobDetail(): CurationJobDetail {
  return {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    warp_ok: true,
    created_at: "2026-06-30T09:00:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        reviewed_at: null,
        top5: [
          { label: "무", sim: 0.77 },
          { label: "배추", sim: 0.21 },
        ],
      },
    ],
  };
}

function patchResult(
  over: Partial<CurationPairPatchResult> = {},
): CurationPairPatchResult {
  return {
    id: 9001,
    crop_ref: "128/0",
    row_index: 0,
    draft_label: "무우",
    final_label: "무",
    canonical_label: "배추",
    supply: 8000,
    status: "included",
    reviewed_at: "2026-06-30T10:00:00",
    job_id: 128,
    ...over,
  };
}

describe("useCurationJob", () => {
  beforeEach(() => vi.clearAllMocks());

  it("성공 PATCH는 응답을 merge하되 top5를 보존한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockPatchPair.mockResolvedValue({ data: patchResult() });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });

    const pair = result.current.job!.pairs[0];
    expect(pair.canonical_label).toBe("배추");
    expect(pair.top5).toHaveLength(2); // 응답에 없던 top5 보존
    expect(pair).not.toHaveProperty("job_id"); // job_id는 병합에서 제외
  });

  it("PATCH 실패 시 직전 값으로 롤백한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockPatchPair.mockRejectedValue(new Error("서버 오류"));
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.patchPair(9001, { status: "excluded" });
    });

    expect(result.current.job!.pairs[0].status).toBe("included"); // 롤백
  });

  it("reviewJob 성공은 true를 반환한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockReviewJob.mockResolvedValue({
      data: { job_id: 128, curation_reviewed: true },
    });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let ok = false;
    await act(async () => {
      ok = await result.current.reviewJob();
    });
    expect(ok).toBe(true);
    expect(mockReviewJob).toHaveBeenCalledWith(128);
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/hooks/use-curation-job.test.ts`
Expected: FAIL — `Cannot find module './use-curation-job'`.

- [ ] **Step 3: 최소 구현**

```ts
// src/hooks/use-curation-job.ts
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import type {
  CurationJobDetail,
  CurationJobPair,
  CurationPairPatch,
} from "@/types/curation";
import { curationAPI } from "@/services/api";

interface UseCurationJobReturn {
  job: CurationJobDetail | null;
  loading: boolean;
  error: string | null;
  patchPair: (id: number, patch: CurationPairPatch) => Promise<void>;
  reviewJob: () => Promise<boolean>;
  refetch: () => void;
}

export function useCurationJob(
  jobId: number | undefined,
): UseCurationJobReturn {
  const [job, setJob] = useState<CurationJobDetail | null>(null);
  const [loading, setLoading] = useState(!!jobId);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJob(jobId);
      setJob(res.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "잡을 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const patchPair = useCallback(
    async (id: number, patch: CurationPairPatch) => {
      let prevPair: CurationJobPair | undefined;
      // 1) 옵티미스틱: 로컬 pair 즉시 불변 갱신.
      setJob((prev) => {
        if (!prev) return prev;
        prevPair = prev.pairs.find((p) => p.id === id);
        return {
          ...prev,
          pairs: prev.pairs.map((p) => (p.id === id ? { ...p, ...patch } : p)),
        };
      });
      try {
        const res = await curationAPI.patchPair(id, patch);
        // 2) 성공: 응답을 merge. job_id는 버리고 top5는 기존 값 보존(계약 비대칭).
        const { job_id: _jobId, ...base } = res.data;
        setJob((prev) =>
          prev
            ? {
                ...prev,
                pairs: prev.pairs.map((p) =>
                  p.id === id ? { ...p, ...base } : p,
                ),
              }
            : prev,
        );
      } catch {
        // 3) 실패: 해당 pair만 직전 값으로 롤백(동시 발행된 다른 pair 변경은 보존) + 에러 토스트.
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
    },
    [],
  );

  const reviewJob = useCallback(async (): Promise<boolean> => {
    if (!jobId) return false;
    try {
      await curationAPI.reviewJob(jobId);
      toast.success("검수가 완료되었습니다");
      return true;
    } catch {
      toast.error("검수 완료에 실패했습니다");
      return false;
    }
  }, [jobId]);

  return { job, loading, error, patchPair, reviewJob, refetch: fetch };
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/hooks/use-curation-job.test.ts`
Expected: PASS (3 tests). 특히 top5 보존·롤백 케이스 통과.

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/hooks/use-curation-job.ts apps/invoice-ocr/frontend/src/hooks/use-curation-job.test.ts
git commit -m "feat(curation): use-curation-job 훅 — 옵티미스틱 PATCH(롤백·top5 보존 merge) + reviewJob"
```

---

## Task 5: `CurationQueuePage` (`/curation`)

**Files:**

- Create: `apps/invoice-ocr/frontend/src/app/curation/page.tsx`
- Test: `apps/invoice-ocr/frontend/src/app/curation/page.test.tsx`

**Interfaces:**

- Consumes: `useCurationJobs`, `EmptyState`, `Pagination`(+ 하위), `PageContainer`/`PageHeader`, react-router `useNavigate`.
- Produces: `export default function CurationQueuePage()`.

- [ ] **Step 1: 실패 테스트 작성 (렌더 스모크)**

```tsx
// src/app/curation/page.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import CurationQueuePage from "./page";
import { useCurationJobs } from "@/hooks/use-curation-jobs";

vi.mock("@/hooks/use-curation-jobs", () => ({ useCurationJobs: vi.fn() }));
const mockHook = vi.mocked(useCurationJobs);

function setup(over: Partial<ReturnType<typeof useCurationJobs>> = {}) {
  mockHook.mockReturnValue({
    data: [],
    total: 0,
    page: 1,
    totalPages: 0,
    loading: false,
    error: null,
    setPage: vi.fn(),
    refetch: vi.fn(),
    ...over,
  });
  return render(
    <MemoryRouter>
      <CurationQueuePage />
    </MemoryRouter>,
  );
}

describe("CurationQueuePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("잡 행과 미검수 배지를 렌더한다", () => {
    setup({
      total: 1,
      data: [
        {
          job_id: 128,
          invoice_id: 341,
          curation_reviewed: false,
          pair_count: 7,
          unreviewed_count: 7,
          created_at: "2026-06-30T09:00:00",
        },
      ],
    });
    expect(screen.getByText("#128")).toBeInTheDocument();
    expect(screen.getByText("● 미검수")).toBeInTheDocument();
  });

  it("빈 큐는 EmptyState를 보여준다", () => {
    setup();
    expect(screen.getByText("검수할 잡이 없습니다")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/app/curation/page.test.tsx`
Expected: FAIL — `Cannot find module './page'`.

- [ ] **Step 3: 최소 구현**

```tsx
// src/app/curation/page.tsx
import { useNavigate } from "react-router-dom";
import { InboxIcon } from "lucide-react";

import { useCurationJobs } from "@/hooks/use-curation-jobs";
import { PageContainer } from "@/components/layout";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

function formatDate(iso: string): string {
  return iso.slice(5, 10); // MM-DD
}

export default function CurationQueuePage() {
  const navigate = useNavigate();
  const { data, total, page, totalPages, loading, error, setPage } =
    useCurationJobs(20);

  return (
    <>
      <PageContainer className="py-4">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="text-xl font-semibold">OCR 학습 큐레이션</h1>
          <span className="text-muted-foreground text-sm">총 {total}건</span>
        </div>

        {error && (
          <p className="text-destructive py-8 text-center text-sm">{error}</p>
        )}

        {loading && (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}

        {!loading && !error && data.length === 0 && (
          <EmptyState
            icon={InboxIcon}
            title="검수할 잡이 없습니다"
            description="confirmed된 OCR 잡이 생기면 여기에 표시됩니다."
          />
        )}

        {!loading && !error && data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-muted-foreground border-b text-left">
              <tr>
                <th className="py-2">잡</th>
                <th>명세서</th>
                <th>행수</th>
                <th>미처리</th>
                <th>상태</th>
                <th>생성일</th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr
                  key={job.job_id}
                  className="hover:bg-muted/50 cursor-pointer border-b"
                  onClick={() => navigate(`/curation/${job.job_id}`)}
                >
                  <td className="py-2 font-medium">#{job.job_id}</td>
                  <td>
                    {job.invoice_id != null ? `inv·${job.invoice_id}` : "—"}
                  </td>
                  <td>{job.pair_count}</td>
                  <td>{job.unreviewed_count}</td>
                  <td>
                    {job.curation_reviewed ? (
                      <span className="text-green-600">✓ 검수됨</span>
                    ) : (
                      <span className="text-amber-600">● 미검수</span>
                    )}
                  </td>
                  <td>{formatDate(job.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {totalPages > 1 && (
          <Pagination className="mt-4">
            <PaginationContent>
              <PaginationItem>
                <PaginationPrevious
                  onClick={() => setPage(Math.max(1, page - 1))}
                />
              </PaginationItem>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <PaginationItem key={p}>
                  <PaginationLink
                    isActive={p === page}
                    onClick={() => setPage(p)}
                  >
                    {p}
                  </PaginationLink>
                </PaginationItem>
              ))}
              <PaginationItem>
                <PaginationNext
                  onClick={() => setPage(Math.min(totalPages, page + 1))}
                />
              </PaginationItem>
            </PaginationContent>
          </Pagination>
        )}
      </PageContainer>
    </>
  );
}
```

배지 텍스트는 e2e와 테스트가 의존하므로 `미검수`/`검수됨` 문자열을 정확히 유지한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/app/curation/page.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/app/curation/page.tsx apps/invoice-ocr/frontend/src/app/curation/page.test.tsx
git commit -m "feat(curation): CurationQueuePage — 검수 큐 목록 + 페이지네이션 + 빈 상태"
```

---

## Task 6: `CurationJobPage` + 행 카드 + 변경 강조 유틸 + 라벨 onCommit

**Files:**

- Create: `apps/invoice-ocr/frontend/src/utils/curation.ts`
- Test: `apps/invoice-ocr/frontend/src/utils/curation.test.ts`
- Modify: `apps/invoice-ocr/frontend/src/components/ui/autocomplete.tsx`(optional `onCommit` prop 추가)
- Create: `apps/invoice-ocr/frontend/src/components/curation/CurationPairRow.tsx`
- Create: `apps/invoice-ocr/frontend/src/app/curation/[jobId]/page.tsx`
- Test: `apps/invoice-ocr/frontend/src/app/curation/[jobId]/page.test.tsx`

**Interfaces:**

- Consumes: `useCurationJob`, `useItems`, `useDebounce`, `Autocomplete`/`AutocompleteSuggestion`, `curationImageUrl`/`curationCropUrl`, `useParams`/`useNavigate`, `isPairChanged`.
- Produces:
  - `utils/curation.ts`: `isLabelCorrected(pair)`, `isLabelRenormalized(pair)`, `isPairChanged(pair)`.
  - `CurationPairRow`: `{ jobId: number; pair: CurationJobPair; onPatch: (id, patch) => void }`.
  - `export default function CurationJobPage()`.

- [ ] **Step 1: 변경 강조 유틸 실패 테스트**

```ts
// src/utils/curation.test.ts
import { describe, it, expect } from "vitest";
import {
  isLabelCorrected,
  isLabelRenormalized,
  isPairChanged,
} from "./curation";

const base = { draft_label: "무", final_label: "무", canonical_label: "무" };

describe("curation 변경 강조 판정", () => {
  it("draft≠final 이면 인식 교정", () => {
    expect(
      isLabelCorrected({ ...base, draft_label: "무우", final_label: "무" }),
    ).toBe(true);
    expect(isLabelCorrected(base)).toBe(false);
  });

  it("final≠canonical 이면 재정규화", () => {
    expect(isLabelRenormalized({ ...base, canonical_label: "배추" })).toBe(
      true,
    );
    expect(isLabelRenormalized(base)).toBe(false);
  });

  it("둘 중 하나라도 변경이면 changed", () => {
    expect(isPairChanged(base)).toBe(false);
    expect(isPairChanged({ ...base, canonical_label: "배추" })).toBe(true);
  });
});
```

- [ ] **Step 2: 유틸 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/utils/curation.test.ts`
Expected: FAIL — `Cannot find module './curation'`.

- [ ] **Step 3: 유틸 구현**

```ts
// src/utils/curation.ts
interface LabelTriplet {
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
}

// 인식 교정: 원시 OCR(draft)과 정제 결과(final)가 다르다.
export function isLabelCorrected(
  pair: Pick<LabelTriplet, "draft_label" | "final_label">,
): boolean {
  return pair.draft_label !== pair.final_label;
}

// 재정규화: 정제 결과(final)와 큐레이터가 정한 정규 라벨(canonical)이 다르다.
export function isLabelRenormalized(
  pair: Pick<LabelTriplet, "final_label" | "canonical_label">,
): boolean {
  return pair.final_label !== pair.canonical_label;
}

export function isPairChanged(pair: LabelTriplet): boolean {
  return isLabelCorrected(pair) || isLabelRenormalized(pair);
}
```

- [ ] **Step 4: 유틸 테스트 통과 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- src/utils/curation.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: `Autocomplete`에 optional `onCommit` 추가 (surgical)**

`src/components/ui/autocomplete.tsx`의 props 타입에 `onCommit?: (value: string) => void;`를 추가하고, 함수 시그니처 구조분해에 `onCommit`을 추가한다. 내부 `<input>`에 `onBlur`를 추가하고, 기존 select `onSelect` 콜백 끝에 `onCommit?.(s.label)`을 호출한다. 팝오버 항목 클릭 시 이벤트 순서가 mousedown→blur→click(onSelect)이라 blur가 먼저 stale 값을 commit해버리는 이중 발화를 막기 위해 `selectingRef` 가드를 둔다.

컴포넌트 본문(45줄 `useState` 근처)에 ref 추가:

```tsx
const selectingRef = React.useRef(false);
```

input 요소(63-73줄)를 다음으로 교체:

```tsx
<input
  id={id}
  name={name}
  className="bg-muted placeholder:text-muted-foreground focus-visible:ring-ring flex h-12 w-full rounded-xl px-4 py-1 text-base transition-[color,box-shadow] outline-none focus-visible:ring-2 md:text-sm"
  value={inputValue}
  onChange={(e) => {
    setInputValue(e.target.value);
    onChange(e.target.value);
    if (!open) setOpen(true);
  }}
  onFocus={() => setOpen(true)}
  onBlur={() => {
    if (selectingRef.current) {
      selectingRef.current = false;
      return;
    }
    onCommit?.(inputValue);
  }}
  placeholder={placeholder}
  aria-label={ariaLabel ?? (id ? undefined : placeholder)}
/>
```

select `CommandItem`(88-95줄)에 mousedown 플래그(blur보다 먼저 실행됨)를 추가하고, `onSelect` 끝에 commit 추가:

```tsx
onMouseDown={() => {
  selectingRef.current = true;
}}
onSelect={() => {
  onChange(s.label, s);
  setInputValue(s.label);
  setOpen(false);
  onCommit?.(s.label);
}}
```

props 타입과 구조분해에 `onCommit?: (value: string) => void;` / `onCommit,` 추가. 기존 호출부(invoice-form 등)는 `onCommit` 미전달 → 동작 무변(`onCommit` 미전달이라 onBlur 분기는 어차피 no-op).

- [ ] **Step 6: 행 카드 + 잡 페이지 실패 테스트**

```tsx
// src/app/curation/[jobId]/page.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import CurationJobPage from "./page";
import { useCurationJob } from "@/hooks/use-curation-job";
import type { CurationJobDetail } from "@/types/curation";

vi.mock("@/hooks/use-curation-job", () => ({ useCurationJob: vi.fn() }));
vi.mock("@/hooks/use-items", () => ({ useItems: () => ({ data: [] }) }));
const mockHook = vi.mocked(useCurationJob);

function job(over: Partial<CurationJobDetail> = {}): CurationJobDetail {
  return {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    warp_ok: false,
    created_at: "2026-06-30T09:00:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        reviewed_at: null,
        top5: [{ label: "무", sim: 0.77 }],
      },
    ],
    ...over,
  };
}

function setup(jobData: CurationJobDetail | null) {
  mockHook.mockReturnValue({
    job: jobData,
    loading: false,
    error: null,
    patchPair: vi.fn(),
    reviewJob: vi.fn().mockResolvedValue(true),
    refetch: vi.fn(),
  });
  return render(
    <MemoryRouter initialEntries={["/curation/128"]}>
      <Routes>
        <Route path="/curation/:jobId" element={<CurationJobPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CurationJobPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("잡 헤더와 행을 렌더한다", () => {
    setup(job());
    expect(screen.getByText(/잡 #128/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "검수 완료" }),
    ).toBeInTheDocument();
  });

  it("warp_ok=false면 워프 placeholder를 보여준다", () => {
    setup(job({ warp_ok: false }));
    expect(screen.getByText("워프 산출 없음")).toBeInTheDocument();
  });
});
```

- [ ] **Step 7: 행 카드 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test -- "src/app/curation/[jobId]/page.test.tsx"`
Expected: FAIL — `Cannot find module './page'`.

- [ ] **Step 8: `CurationPairRow` 구현**

```tsx
// src/components/curation/CurationPairRow.tsx
import { useState } from "react";

import type { CurationJobPair, CurationPairPatch } from "@/types/curation";
import type { AutocompleteSuggestion } from "@/components/ui/autocomplete";
import { Autocomplete } from "@/components/ui/autocomplete";
import { Button } from "@/components/ui/button";
import { useItems } from "@/hooks/use-items";
import { useDebounce } from "@/hooks/use-debounce";
import { curationCropUrl } from "@/services/api";
import { isPairChanged } from "@/utils/curation";
import { cn } from "@/lib/utils";

const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="40"><rect width="64" height="40" fill="#e5e7eb"/></svg>',
  );

interface CurationPairRowProps {
  jobId: number;
  pair: CurationJobPair;
  onPatch: (id: number, patch: CurationPairPatch) => void;
}

export function CurationPairRow({
  jobId,
  pair,
  onPatch,
}: CurationPairRowProps) {
  const [labelQuery, setLabelQuery] = useState(pair.canonical_label ?? "");
  const debounced = useDebounce(labelQuery, 250);
  const { data: items } = useItems(debounced);

  const suggestions: AutocompleteSuggestion[] = (items ?? []).map((it) => ({
    label: it.item_name,
    value: String(it.id ?? it.item_name),
  }));

  const excluded = pair.status === "excluded";

  const commitLabel = (value: string) => {
    const next = value.trim();
    if (next && next !== (pair.canonical_label ?? "")) {
      onPatch(pair.id, { canonical_label: next });
    }
  };

  const toggleExcluded = () => {
    onPatch(pair.id, { status: excluded ? "included" : "excluded" });
  };

  return (
    <div
      className={cn(
        "flex flex-col gap-2 border-b py-3 sm:flex-row sm:items-start",
        excluded && "opacity-50",
      )}
      data-testid={`pair-${pair.id}`}
    >
      <img
        src={curationCropUrl(jobId, pair.row_index)}
        alt={`행 ${pair.row_index} crop`}
        className="h-10 w-16 rounded border object-cover"
        onError={(e) => {
          e.currentTarget.src = PLACEHOLDER;
        }}
      />
      <div className="flex-1">
        <div className="text-muted-foreground mb-1 text-xs">
          #{pair.row_index} · top5:{" "}
          {pair.top5.map((t) => t.label).join("·") || "—"}
          {isPairChanged(pair) && (
            <span className="ml-1 text-amber-600">✎ 변경</span>
          )}
        </div>
        <Autocomplete
          value={labelQuery}
          onChange={setLabelQuery}
          onCommit={commitLabel}
          suggestions={suggestions}
          placeholder="라벨"
          ariaLabel={`행 ${pair.row_index} 라벨`}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">
          {pair.supply != null ? pair.supply.toLocaleString() : "—"}
          <span className="ml-1 text-xs">학습 비대상</span>
        </span>
        <Button size="sm" variant="outline" onClick={toggleExcluded}>
          {excluded ? "포함" : "제외"}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 9: `CurationJobPage` 구현**

```tsx
// src/app/curation/[jobId]/page.tsx
import { useNavigate, useParams } from "react-router-dom";

import { useCurationJob } from "@/hooks/use-curation-job";
import { CurationPairRow } from "@/components/curation/CurationPairRow";
import { PageContainer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { curationImageUrl } from "@/services/api";

const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#e5e7eb"/></svg>',
  );

export default function CurationJobPage() {
  const { jobId } = useParams();
  const numericId = jobId ? Number(jobId) : undefined;
  const navigate = useNavigate();
  const { job, loading, error, patchPair, reviewJob } =
    useCurationJob(numericId);

  const handleReview = async () => {
    const ok = await reviewJob();
    if (ok) navigate("/curation");
  };

  if (loading) {
    return (
      <PageContainer className="py-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-64 w-full" />
      </PageContainer>
    );
  }

  if (error || !job || numericId === undefined) {
    return (
      <PageContainer className="py-4">
        <p className="text-destructive text-center text-sm">
          {error ?? "잡을 찾을 수 없습니다"}
        </p>
      </PageContainer>
    );
  }

  return (
    <>
      <PageContainer className="py-4">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-xl font-semibold">
            잡 #{job.job_id}
            {job.invoice_id != null && (
              <span className="text-muted-foreground ml-2 text-sm">
                (inv·{job.invoice_id})
              </span>
            )}
          </h1>
          <Button onClick={handleReview} disabled={job.curation_reviewed}>
            검수 완료
          </Button>
        </div>

        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          {/* 좌: 단계 이미지 */}
          <div className="space-y-3">
            <div>
              <p className="text-muted-foreground mb-1 text-xs">① 원본</p>
              <img
                src={curationImageUrl(job.job_id, "original")}
                alt="원본 전표"
                className="w-full rounded border"
                onError={(e) => {
                  e.currentTarget.src = PLACEHOLDER;
                }}
              />
            </div>
            <div>
              <p className="text-muted-foreground mb-1 text-xs">② Warp</p>
              {job.warp_ok ? (
                <img
                  src={curationImageUrl(job.job_id, "warped")}
                  alt="워프 전표"
                  className="w-full rounded border"
                  onError={(e) => {
                    e.currentTarget.src = PLACEHOLDER;
                  }}
                />
              ) : (
                <div className="bg-muted text-muted-foreground flex h-32 items-center justify-center rounded border text-sm">
                  워프 산출 없음
                </div>
              )}
            </div>
          </div>

          {/* 우: 행별 학습쌍 */}
          <div>
            <h2 className="mb-2 text-sm font-semibold">행별 학습쌍</h2>
            {job.pairs.map((pair) => (
              <CurationPairRow
                key={pair.id}
                jobId={job.job_id}
                pair={pair}
                onPatch={patchPair}
              />
            ))}
          </div>
        </div>
      </PageContainer>
    </>
  );
}
```

- [ ] **Step 10: 행 카드 + 잡 페이지 테스트 통과 + 빌드**

Run: `cd apps/invoice-ocr/frontend && npm run test -- "src/app/curation/[jobId]/page.test.tsx" src/utils/curation.test.ts && npm run build`
Expected: 테스트 PASS, `tsc -b && vite build` 성공.

- [ ] **Step 11: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/utils/curation.ts apps/invoice-ocr/frontend/src/utils/curation.test.ts apps/invoice-ocr/frontend/src/components/ui/autocomplete.tsx apps/invoice-ocr/frontend/src/components/curation/CurationPairRow.tsx "apps/invoice-ocr/frontend/src/app/curation/[jobId]/page.tsx" "apps/invoice-ocr/frontend/src/app/curation/[jobId]/page.test.tsx"
git commit -m "feat(curation): CurationJobPage + 행 카드(옵티미스틱 라벨/제외) + 변경 강조 유틸 + Autocomplete onCommit"
```

---

## Task 7: 라우팅 등록 + 설정 보조 링크

**Files:**

- Modify: `apps/invoice-ocr/frontend/src/main.tsx`(lazy 라우트 2개)
- Modify: `apps/invoice-ocr/frontend/src/app/settings/page.tsx`(하단 보조 링크)

**Interfaces:**

- Consumes: `CurationQueuePage`(Task 5), `CurationJobPage`(Task 6), `Link`(react-router-dom).

- [ ] **Step 1: 실패 테스트 작성 (라우팅 e2e 스텁 — 직접 네비게이션)**

`tests/e2e/curation.spec.ts`에 라우팅만 검증하는 테스트를 먼저 추가한다(Task 8에서 같은 파일을 확장).

```ts
// tests/e2e/curation.spec.ts
import { test, expect } from "@playwright/test";

test("설정 → 큐레이션 링크 → /curation 진입", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("link", { name: "OCR 학습 큐레이션" }).click();
  await expect(page).toHaveURL(/\/curation$/);
  await expect(
    page.getByRole("heading", { name: "OCR 학습 큐레이션" }),
  ).toBeVisible();
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/frontend && npm run test:e2e -- curation.spec.ts`
Expected: FAIL — 설정 페이지에 링크 없음 / `/curation` 라우트 미등록.

- [ ] **Step 3: `main.tsx` 라우트 등록**

lazy import 블록(13-16줄)에 추가:

```tsx
const CurationQueuePage = lazy(() => import("@/app/curation/page"));
const CurationJobPage = lazy(() => import("@/app/curation/[jobId]/page"));
```

`children` 배열의 `/sales-performance` 항목 뒤에 추가:

```tsx
{
  path: "/curation",
  element: (
    <Suspense fallback={LazyFallback}>
      <CurationQueuePage />
    </Suspense>
  ),
},
{
  path: "/curation/:jobId",
  element: (
    <Suspense fallback={LazyFallback}>
      <CurationJobPage />
    </Suspense>
  ),
},
```

- [ ] **Step 4: 설정 페이지 보조 링크 추가**

`src/app/settings/page.tsx` 상단 import에 추가:

```tsx
import { Link } from "react-router-dom";
```

`<div className="space-y-6 lg:grid ...">` 블록을 감싸는 `</PageContainer>` 직전(즉 그리드 div 닫힘 뒤)에 데스크톱 한정 보조 링크를 추가:

```tsx
<div className="mt-8 hidden border-t pt-4 lg:block">
  <Link
    to="/curation"
    className="text-muted-foreground hover:text-foreground text-sm underline"
  >
    OCR 학습 큐레이션
  </Link>
</div>
```

메인 nav(`TopNav`/`BottomNav`)는 건드리지 않는다.

- [ ] **Step 5: 라우팅 e2e 통과 확인 + 빌드**

Run: `cd apps/invoice-ocr/frontend && npm run build && npm run test:e2e -- curation.spec.ts`
Expected: build 성공, e2e 1 test PASS.

- [ ] **Step 6: 커밋**

```bash
git add apps/invoice-ocr/frontend/src/main.tsx apps/invoice-ocr/frontend/src/app/settings/page.tsx apps/invoice-ocr/frontend/tests/e2e/curation.spec.ts
git commit -m "feat(curation): /curation·/curation/:jobId lazy 라우트 + 설정 하단 보조 링크(데스크톱)"
```

---

## Task 8: playwright mock e2e — 큐 → 드릴다운 → 검수완료

**Files:**

- Modify: `apps/invoice-ocr/frontend/tests/e2e/curation.spec.ts`(플로우 테스트 추가)

**Interfaces:**

- Consumes: mock `curationAPI`(Task 2, `VITE_USE_MOCK=true`), 시드 잡 #128(미검수)·#127(검수됨).

- [ ] **Step 1: 실패 테스트 작성 (mock 플로우)**

`tests/e2e/curation.spec.ts`에 추가:

```ts
test("큐 → 드릴다운(라벨/제외) → 검수완료 → 큐에서 검수됨 확인", async ({
  page,
}) => {
  await page.goto("/curation");
  // 미검수-우선 정렬: #128이 먼저, 미검수 배지 노출.
  await expect(page.getByText("#128")).toBeVisible();
  await expect(page.getByText("● 미검수")).toBeVisible();

  // 드릴다운.
  await page.getByText("#128").click();
  await expect(page).toHaveURL(/\/curation\/128$/);
  await expect(page.getByRole("heading", { name: /잡 #128/ })).toBeVisible();

  // 라벨 편집 → blur로 옵티미스틱 PATCH.
  // mock 시드 #128은 row_index 0·1 두 pair를 가진다(mocks/curation.ts). 시드 변경 시 이 셀렉터도 동반 수정.
  const labelInput = page.getByLabel("행 1 라벨");
  await labelInput.fill("배추");
  await labelInput.blur();

  // 제외 토글 → 옵티미스틱 PATCH.
  await page.getByRole("button", { name: "제외" }).first().click();
  await expect(
    page.getByRole("button", { name: "포함" }).first(),
  ).toBeVisible();

  // 검수 완료 → 큐로 복귀.
  await page.getByRole("button", { name: "검수 완료" }).click();
  await expect(page).toHaveURL(/\/curation$/);

  // 잡 #128이 이제 검수됨으로 내려간다.
  await expect(
    page.getByRole("row", { name: /#128/ }).getByText("✓ 검수됨"),
  ).toBeVisible();
});

test("이미지 mock 불가 → 워프 placeholder degrade", async ({ page }) => {
  await page.goto("/curation/127"); // warp_ok=false 시드.
  await expect(page.getByText("워프 산출 없음")).toBeVisible();
});
```

- [ ] **Step 2: 테스트 실패 확인(있다면)**

Run: `cd apps/invoice-ocr/frontend && npm run test:e2e -- curation.spec.ts`
Expected: 신규 2개 중 실패가 있으면 셀렉터/플로우를 mock 동작에 맞게 교정(예: 행 인덱스, 배지 텍스트). RED를 먼저 확인한 뒤 진행한다.

- [ ] **Step 3: 통과시키기 (셀렉터/시드 정합 조정)**

테스트가 통과하도록 다음을 조정한다(코드 변경이 아니라 정합 맞추기):

- mock 시드 #128에 행 인덱스 1 pair(`9002`)가 있으므로 `getByLabel("행 1 라벨")`이 매칭된다(Task 2 시드 확인).
- 배지 문자열 `● 미검수`/`✓ 검수됨`이 Task 5 구현과 정확히 일치하는지 확인.
- `제외`/`포함` 버튼 토글 라벨이 Task 6 `CurationPairRow`와 일치하는지 확인.

필요 시 Task 5/6의 텍스트를 e2e 기대값에 맞춰 1곳만 수정(드리프트 금지).

- [ ] **Step 4: 전체 e2e + 단위 + 게이트 통과 확인**

Run:

```bash
cd apps/invoice-ocr/frontend && npm run test:e2e -- curation.spec.ts && npm run test && npm run lint && npm run format:check && npm run build
```

Expected: e2e 전부 PASS, vitest 전부 PASS, lint/format 0 error, build 성공.

- [ ] **Step 5: 커밋**

```bash
git add apps/invoice-ocr/frontend/tests/e2e/curation.spec.ts
git commit -m "test(curation): mock e2e — 큐→드릴다운(라벨·제외)→검수완료 + 이미지 placeholder degrade"
```

---

## Self-Review

**1. Spec 커버리지 (섹션별 → task 매핑)**

- §2 아키텍처·라우팅(lazy 2 라우트, nav 미포함) → Task 7. ✅
- §3.1 `types/curation.ts`(api-spec 미러, 계약 비대칭) → Task 1. ✅
- §3.2 `curationAPI`(JSON 4종 mock proxy + URL 빌더 real-only) → Task 2. ✅
- §3.3 훅 2종 + 옵티미스틱 PATCH(롤백·top5 보존 merge·invoice 미변경) → Task 3·4. ✅
- §4.1 `CurationQueuePage`(페이지네이션·배지·빈 상태·미검수 우선 정렬은 서버/mock 정렬) → Task 5. ✅
- §4.2 `CurationJobPage`(2단·행 카드·crop/단계 이미지·top5 칩·라벨 자동완성·제외 토글·금액·변경 강조·검수완료·워프 placeholder) → Task 6. ✅
- §4.3 설정 보조 링크(데스크톱) → Task 7. ✅
- §5 테스트(vitest 훅·URL 빌더·변경 강조 / playwright mock 플로우 / 이미지 placeholder) → Task 1-6 단위 + Task 8 e2e. ✅
- §6 비범위(유사 라벨 힌트·상태 서버 필터·라이브 관통 e2e·일괄 병합·재학습·모바일 분기) → 계획에 포함하지 않음(범위 준수). ✅

**2. Placeholder 스캔**

모든 코드 스텝은 실제 코드 포함. "적절히 처리"류 문구 없음. e2e 셀렉터 교정 스텝(Task 8 Step 3)은 코드가 아니라 정합 맞추기로 명시. ✅

**3. 타입 일관성**

- `useCurationJobs` 반환(`data/total/page/totalPages/loading/error/setPage/refetch`) — Task 3 정의와 Task 5 소비 일치.
- `useCurationJob` 반환(`job/loading/error/patchPair/reviewJob/refetch`) — Task 4 정의와 Task 6 소비 일치.
- `patchPair(id, patch)` 시그니처 — Task 4·6 일치. `reviewJob(): Promise<boolean>` — Task 4 정의, Task 6 `navigate` 분기 일치.
- `curationImageUrl(jobId, kind)`·`curationCropUrl(jobId, row)` — Task 2 정의, Task 6 소비 일치.
- `CurationJobPair.top5` 보존 / `CurationPairPatchResult.job_id` 제외 merge — Task 1 타입 ↔ Task 2 mock ↔ Task 4 훅 일치.
- `Autocomplete.onCommit` optional prop — Task 6에서 추가, 기존 호출부 무영향(prop optional). ✅

**해소하지 못한 가정 / 리스크**

1. **URL 빌더 이름**: spec의 `curationAPI.imageUrl`/`cropUrl`을 standalone `curationImageUrl`/`curationCropUrl`로 명시화(mock proxy 오염 회피). 계약·경로 동일, 이름만 변경. 리뷰에서 명명 반려 시 `curationAPI`에 비-proxy 속성으로 재배치 가능.
2. **`Autocomplete` blur 의미론**: 기존 컴포넌트에 `onCommit` 1개 prop을 추가하는 surgical 변경. 공용 컴포넌트 수정이 부담되면 Task 6에서 `CurationPairRow` 전용 라벨 입력(native `<input list>` datalist)으로 대체 가능 — 단 "Combobox 재사용" 의도와 멀어진다.
3. **검수완료 멱등/ack 형태**: 검수완료 ack `{job_id, curation_reviewed}`는 **api-spec review 200 data 스키마로 확정**(가정 아님). 소비처는 boolean만 사용한다.
4. **프론트 커버리지 게이트 부재**: 페이지 상호작용은 e2e가 정본 검증. 백엔드와 달리 frontend CI에 `--cov-fail-under`가 없어, 페이지 단위 테스트는 렌더 스모크로 한정함(의도적).
5. **e2e 행 인덱스 의존**: Task 8은 mock 시드 #128의 `행 1`(row_index 1) pair 존재에 의존. 시드 변경 시 셀렉터 동반 수정 필요(Task 2·8 동기).
