import { act, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useCurationJobs } from "@/hooks/use-curation-jobs";
import { useCurationJob } from "@/hooks/use-curation-job";
import { useUnconfirmedJobs } from "@/hooks/use-unconfirmed-jobs";
import { ocrAPI } from "@/services/api";
import type { CurationJobDetail } from "@/types/curation";

vi.mock("@/hooks/use-curation-jobs", () => ({ useCurationJobs: vi.fn() }));
vi.mock("@/hooks/use-curation-job", () => ({ useCurationJob: vi.fn() }));
vi.mock("@/hooks/use-unconfirmed-jobs", () => ({
  useUnconfirmedJobs: vi.fn(),
}));
// 주의: 확정 전 상세는 훅이 아니라 ocrAPI를 직접 부른다. 이 vi.mock은 파일 전역이라
// "@/services/api"를 통째로 대체하면 같은 파일의 /curation/42 테스트가 렌더하는
// app/curation/[jobId]/page.tsx의 curationImageUrl(...)이 undefined 호출로 죽는다.
// → 반드시 원본을 스프레드하고 필요한 것만 덮는다.
vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return { ...actual, ocrAPI: { ...actual.ocrAPI, getJob: vi.fn() } };
});

const mockUseCurationJobs = vi.mocked(useCurationJobs);
const mockUseCurationJob = vi.mocked(useCurationJob);
const mockUseUnconfirmedJobs = vi.mocked(useUnconfirmedJobs);

function job(over: Partial<CurationJobDetail> = {}): CurationJobDetail {
  return {
    job_id: 42,
    invoice_id: null,
    curation_reviewed: false,
    curation_reviewed_at: null,
    warp_ok: false,
    created_at: "2026-06-30T09:00:00",
    pairs: [],
    ...over,
  };
}

// main.tsx는 import 시 createRoot().render()를 즉시 실행하는 엔트리 파일이라
// 실제 라우트 등록을 검증하려면 #root 컨테이너 + 진입 경로를 미리 만들어둔 뒤
// 모듈을 새로 import해야 한다(테스트마다 vi.resetModules 필요).
async function bootMainAt(path: string) {
  document.body.innerHTML = '<div id="root"></div>';
  window.history.pushState({}, "", path);
  vi.resetModules();
  await act(async () => {
    await import("./main");
  });
}

describe("main.tsx 라우트 등록 (/curation, /curation/:jobId)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
  });

  it("/curation 경로에서 CurationQueuePage를 렌더한다", async () => {
    mockUseCurationJobs.mockReturnValue({
      data: [],
      total: 0,
      page: 1,
      totalPages: 0,
      loading: false,
      error: null,
      setPage: vi.fn(),
      refetch: vi.fn(),
    });
    await bootMainAt("/curation");
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "OCR 학습 큐레이션" }),
      ).toBeInTheDocument(),
    );
  });

  it("/curation/:jobId 경로에서 jobId 파라미터로 CurationJobPage를 렌더한다", async () => {
    mockUseCurationJob.mockReturnValue({
      job: job({ job_id: 42 }),
      loading: false,
      error: null,
      patchPair: vi.fn(),
      reviewJob: vi.fn(),
      refetch: vi.fn(),
    });
    await bootMainAt("/curation/42");
    await waitFor(() => expect(screen.getByText(/잡 #42/)).toBeInTheDocument());
    expect(mockUseCurationJob).toHaveBeenCalledWith(42);
  });

  it("/curation/pending 경로에서 확정 전 관측 목록을 렌더한다", async () => {
    mockUseUnconfirmedJobs.mockReturnValue({
      data: [],
      total: 0,
      page: 1,
      totalPages: 0,
      loading: false,
      error: null,
      setPage: vi.fn(),
      refetch: vi.fn(),
    });
    await bootMainAt("/curation/pending");
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "확정 전 잡 관측" }),
      ).toBeInTheDocument(),
    );
  });

  it("/curation/pending/:jobId 경로에서 jobId로 관측 상세를 렌더한다", async () => {
    vi.mocked(ocrAPI.getJob).mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: { rows: [], supply_sum: 0, warp_ok: true },
      },
    });
    await bootMainAt("/curation/pending/42");
    await waitFor(() => expect(screen.getByText(/잡 #42/)).toBeInTheDocument());
    expect(vi.mocked(ocrAPI.getJob)).toHaveBeenCalledWith(42);
  });
});
