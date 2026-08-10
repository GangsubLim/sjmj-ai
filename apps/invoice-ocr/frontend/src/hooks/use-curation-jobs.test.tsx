import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
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
    curation_reviewed_at: null,
    pair_count: 3,
    unreviewed_count: 3,
    created_at: "2026-06-30T09:00:00",
    ...over,
  };
}

function listResponse(data: CurationJobSummary[], total = data.length) {
  return { data, pagination: { page: 1, limit: 20, total, totalPages: 1 } };
}

// page를 URL이 소유하게 되면서 이 훅은 Router context를 요구한다.
function renderJobs(limit?: number, entry = "/curation") {
  return renderHook(() => useCurationJobs(limit), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
    ),
  });
}

describe("useCurationJobs", () => {
  beforeEach(() => vi.clearAllMocks());

  it("잡 목록과 total을 노출한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([summary({ job_id: 128 })], 42));
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.total).toBe(42);
  });

  it("URL의 page로 초기 조회한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));
    renderJobs(20, "/curation?page=3");
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 3, limit: 20 }),
    );
  });

  it("setPage가 URL을 바꿔 재조회를 일으킨다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));
    const { result } = renderJobs(20);
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 1, limit: 20 }),
    );
    act(() => result.current.setPage(2));
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 2, limit: 20 }),
    );
    expect(result.current.page).toBe(2);
  });

  it("에러 메시지를 한국어로 노출한다", async () => {
    mockGetJobs.mockRejectedValue(new Error("조회 실패"));
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("조회 실패");
  });
});
