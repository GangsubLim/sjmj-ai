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
