import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import CurationQueuePage from "./page";
import CurationJobPage from "./[jobId]/page";
import { curationAPI } from "@/services/api";
import { useCurationJob } from "@/hooks/use-curation-job";
import {
  useJobNeighbors,
  fetchCurationRowDeltaPage,
} from "@/hooks/use-job-neighbors";
import type { CurationJobDetail } from "@/types/curation";

// CurationJobPage가 끌고 오는 모듈들이 named import로 쓰는 것을 모두 채워야 한다 —
// JobImagePanel은 curationImageUrl, CurationPairRow는 ocrCropUrl을 import한다.
vi.mock("@/services/api", () => ({
  curationAPI: { getJobs: vi.fn() },
  curationImageUrl: (jobId: number, kind: string) =>
    `/api/curation/jobs/${jobId}/image/${kind}`,
  ocrCropUrl: (jobId: number, row: number) =>
    `/api/ocr/jobs/${jobId}/crop/${row}`,
}));
vi.mock("@/hooks/use-curation-job", () => ({ useCurationJob: vi.fn() }));
vi.mock("@/hooks/use-items", () => ({ useItems: () => ({ data: [] }) }));
vi.mock("@/hooks/use-job-neighbors", () => ({
  useJobNeighbors: vi.fn(),
  fetchCurationPage: vi.fn(),
  fetchCurationRowDeltaPage: vi.fn(),
}));

const mockGetJobs = vi.mocked(curationAPI.getJobs);
const mockJob = vi.mocked(useCurationJob);
const mockNeighbors = vi.mocked(useJobNeighbors);

function detail(jobId: number): CurationJobDetail {
  return {
    job_id: jobId,
    invoice_id: 341,
    status: "done",
    curation_reviewed: false,
    curation_reviewed_at: null,
    warp_ok: true,
    created_at: "2026-06-30T09:00:00",
    job_token: "1000",
    pairs: [],
  };
}

describe("큐레이션 목록 위치 보존 수용 흐름", () => {
  beforeEach(() => vi.clearAllMocks());

  it("3페이지 목록 → 상세 → 다음 잡 → 뒤로가기가 3페이지 목록으로 복귀한다", async () => {
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [
        {
          job_id: 128,
          invoice_id: 341,
          curation_reviewed: false,
          curation_reviewed_at: null,
          pair_count: 1,
          unreviewed_count: 1,
          rows_added: null,
          rows_dropped: null,
          created_at: "2026-06-30T09:00:00",
        },
      ],
      pagination: { page: 3, limit: 20, total: 100, totalPages: 5 },
    });
    mockJob.mockImplementation((jobId) => ({
      job: jobId === undefined ? null : detail(jobId),
      loading: false,
      error: null,
      patchPair: vi.fn(),
      reviewJob: vi.fn().mockResolvedValue(true),
      refetch: vi.fn(),
    }));
    mockNeighbors.mockReturnValue({
      prev: null,
      next: { jobId: 129, page: 3 },
      loading: false,
    });

    const router = createMemoryRouter(
      [
        { path: "/curation", element: <CurationQueuePage /> },
        { path: "/curation/:jobId", element: <CurationJobPage /> },
      ],
      { initialEntries: ["/curation?page=3"] },
    );
    render(<RouterProvider router={router} />);

    // 1) 3페이지 목록이 URL의 page로 조회된다
    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 3, limit: 20 }),
    );

    // 2) 행 클릭 → 상세(push). URL에 page=3이 실린다
    fireEvent.click(screen.getByRole("button", { name: "잡 #128 상세" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation/128"),
    );
    expect(router.state.location.search).toBe("?page=3");

    // 3) 다음 잡(replace) — history 길이를 늘리지 않는다
    fireEvent.click(screen.getByRole("button", { name: "다음 →" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation/129"),
    );

    // 4) 브라우저 뒤로가기 한 번 → 보던 3페이지 목록
    await act(async () => {
      await router.navigate(-1);
    });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation"),
    );
    expect(router.state.location.search).toBe("?page=3");
    expect(screen.getByText("OCR 학습 큐레이션")).toBeInTheDocument();
  });

  it("페이지네이션 클릭이 URL의 page를 바꿔 재조회를 일으킨다", async () => {
    // 확정 후 목록의 PaginationLink onClick → setPage → URL 배선. 훅 테스트의
    // setPage 호출만으로는 이 JSX 배선이 보장되지 않는다(spec §6).
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [],
      pagination: { page: 3, limit: 20, total: 100, totalPages: 5 },
    });

    const router = createMemoryRouter(
      [{ path: "/curation", element: <CurationQueuePage /> }],
      { initialEntries: ["/curation?page=3"] },
    );
    render(<RouterProvider router={router} />);

    // PaginationLink는 href 없는 <a>라 role="link"가 아니다 — 페이지 탐색 nav 안에서
    // 텍스트로 찾는다.
    const nav = await screen.findByRole("navigation", { name: "페이지 탐색" });
    fireEvent.click(within(nav).getByText("4"));

    await waitFor(() => expect(router.state.location.search).toBe("?page=4"));
    expect(mockGetJobs).toHaveBeenCalledWith({ page: 4, limit: 20 });
  });

  it("필터 on 목록 → 상세 → 다음 → 목록 복귀까지 row_delta가 유지되고 이웃도 필터된 목록에서 계산된다", async () => {
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [
        {
          job_id: 128,
          invoice_id: 341,
          curation_reviewed: false,
          curation_reviewed_at: null,
          pair_count: 1,
          unreviewed_count: 1,
          rows_added: 2,
          rows_dropped: 0,
          created_at: "2026-06-30T09:00:00",
        },
      ],
      pagination: { page: 1, limit: 20, total: 1, totalPages: 1 },
    });
    mockJob.mockImplementation((jobId) => ({
      job: jobId === undefined ? null : detail(jobId),
      loading: false,
      error: null,
      patchPair: vi.fn(),
      reviewJob: vi.fn().mockResolvedValue(true),
      refetch: vi.fn(),
    }));
    mockNeighbors.mockReturnValue({
      prev: null,
      next: { jobId: 129, page: 1 },
      loading: false,
    });

    const router = createMemoryRouter(
      [
        { path: "/curation", element: <CurationQueuePage /> },
        { path: "/curation/:jobId", element: <CurationJobPage /> },
      ],
      { initialEntries: ["/curation?row_delta=true"] },
    );
    render(<RouterProvider router={router} />);

    // 목록 → 상세: 필터가 상세 URL로 따라간다.
    fireEvent.click(
      await screen.findByRole("button", { name: "잡 #128 상세" }),
    );
    await waitFor(() =>
      expect(router.state.location.search).toBe("?row_delta=true"),
    );
    // 이웃 계산도 필터된 목록에서 이뤄져야 큐가 상세에서 끊기지 않는다.
    expect(mockNeighbors).toHaveBeenLastCalledWith(
      expect.objectContaining({ fetchPage: fetchCurationRowDeltaPage }),
    );

    // 상세 → 다음 잡: 필터 유지.
    fireEvent.click(screen.getByRole("button", { name: "다음 →" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation/129"),
    );
    expect(router.state.location.search).toBe("?row_delta=true");

    // 다음 잡 → 목록 복귀: 필터 유지.
    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation"),
    );
    expect(router.state.location.search).toBe("?row_delta=true");
  });
});
