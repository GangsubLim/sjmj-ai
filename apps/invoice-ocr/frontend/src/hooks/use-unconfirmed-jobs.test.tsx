import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { useUnconfirmedJobs } from "./use-unconfirmed-jobs";
import { ocrAPI } from "@/services/api";
import type { UnconfirmedJobSummary } from "@/types/observation";

vi.mock("@/services/api", () => ({
  ocrAPI: { getUnconfirmedJobs: vi.fn() },
}));

const mockGetJobs = vi.mocked(ocrAPI.getUnconfirmedJobs);

function summary(
  over: Partial<UnconfirmedJobSummary> = {},
): UnconfirmedJobSummary {
  return {
    job_id: 1,
    observation_status: "unconfirmed",
    row_count: 3,
    error: null,
    created_at: "2026-08-01T09:00:00",
    ...over,
  };
}

function listResponse(data: UnconfirmedJobSummary[], total = data.length) {
  return {
    success: true,
    data,
    pagination: { page: 1, limit: 20, total, totalPages: 1 },
  };
}

function renderJobs(limit?: number, entry = "/curation/pending") {
  return renderHook(() => useUnconfirmedJobs(limit), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
    ),
  });
}

describe("useUnconfirmedJobs", () => {
  beforeEach(() => vi.clearAllMocks());

  it("미확정 잡 목록과 total을 노출한다", async () => {
    mockGetJobs.mockResolvedValue(
      listResponse(
        [summary({ job_id: 128, observation_status: "demoted" })],
        42,
      ),
    );
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data[0].observation_status).toBe("demoted");
    expect(result.current.total).toBe(42);
  });

  it("URL의 page로 초기 조회한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));
    renderJobs(20, "/curation/pending?page=3");
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 3, limit: 20 }),
    );
  });

  it("setPage가 URL을 바꿔 재조회를 일으킨다", async () => {
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [],
      pagination: { page: 1, limit: 20, total: 21, totalPages: 2 },
    });
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

  it("에러 메시지를 노출한다", async () => {
    mockGetJobs.mockRejectedValue(new Error("조회 실패"));
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("조회 실패");
  });

  it("에러가 Error 인스턴스가 아니면 기본 문구로 닫는다", async () => {
    mockGetJobs.mockRejectedValue("boom");
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("확정 전 잡을 불러올 수 없습니다");
  });

  it("data가 배열이 아니면 빈 배열로 닫는다", async () => {
    mockGetJobs.mockResolvedValue({
      success: true,
      data: null,
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getUnconfirmedJobs>>);
    const { result } = renderJobs();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual([]);
    expect(result.current.total).toBe(0);
    expect(result.current.totalPages).toBe(0);
  });
});
