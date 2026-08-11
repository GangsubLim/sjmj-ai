import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import UnconfirmedJobsPage from "./page";
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

function renderPage(entry = "/curation/pending") {
  const router = createMemoryRouter(
    [
      { path: "/curation/pending", element: <UnconfirmedJobsPage /> },
      { path: "/curation/pending/:jobId", element: <div>확정 전 상세</div> },
      { path: "/curation/:jobId", element: <div>확정 후 상세</div> },
    ],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("UnconfirmedJobsPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("관측 상태 배지를 한국어로 그린다", async () => {
    mockGetJobs.mockResolvedValue(
      listResponse([
        summary({ job_id: 11, observation_status: "demoted", row_count: 0 }),
        summary({ job_id: 12, observation_status: "no_warp", row_count: 0 }),
        summary({ job_id: 13, observation_status: "pending", row_count: null }),
      ]),
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("강등")).toBeInTheDocument());
    // 워프 없음은 강등과 다른 배지로 표시된다(spec 수용 기준 3).
    expect(screen.getByText("워프 없음")).toBeInTheDocument();
    expect(screen.getByText("대기")).toBeInTheDocument();
    expect(screen.getAllByText("08-01 09:00").length).toBeGreaterThan(0);
  });

  it("모르는 관측 상태 코드는 빈 칸이 아니라 코드 그대로 그린다", async () => {
    mockGetJobs.mockResolvedValue(
      listResponse([
        summary({
          job_id: 41,
          // 백엔드가 9번째 코드를 추가해도 관측 축이 조용히 사라지면 안 된다.
          observation_status:
            "quarantined" as UnconfirmedJobSummary["observation_status"],
        }),
      ]),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("quarantined")).toBeInTheDocument(),
    );
  });

  it("row_count가 null이면 0이 아니라 —로 그린다", async () => {
    mockGetJobs.mockResolvedValue(
      listResponse([
        summary({
          job_id: 21,
          observation_status: "no_result",
          row_count: null,
        }),
      ]),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("결과 없음")).toBeInTheDocument(),
    );
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("실패 잡은 error 문자열을 함께 보인다", async () => {
    mockGetJobs.mockResolvedValue(
      listResponse([
        summary({
          job_id: 31,
          observation_status: "failed",
          row_count: null,
          error: "warp 실패",
        }),
      ]),
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("실패")).toBeInTheDocument());
    expect(screen.getByText("warp 실패")).toBeInTheDocument();
  });

  it("빈 목록이면 EmptyState를 보인다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));

    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText("확정을 기다리는 잡이 없습니다"),
      ).toBeInTheDocument(),
    );
  });

  it("조회 실패 시 에러 문구를 보인다", async () => {
    mockGetJobs.mockRejectedValue(new Error("조회 실패"));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("조회 실패")).toBeInTheDocument(),
    );
  });

  it("확정 전 탭이 활성 상태로 함께 렌더된다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "확정 전" })).toHaveAttribute(
        "data-state",
        "active",
      ),
    );
    expect(screen.getByRole("tab", { name: "확정 후" })).toBeInTheDocument();
  });

  it("정적 세그먼트가 동적 :jobId보다 먼저 매칭된다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));

    renderPage();

    // /curation/pending 이 /curation/:jobId 로 잘못 잡히지 않는다(A7).
    await waitFor(() =>
      expect(screen.queryByText("확정 후 상세")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("확정 전 잡 관측")).toBeInTheDocument();
  });

  it("URL의 page로 초기 조회한다", async () => {
    mockGetJobs.mockResolvedValue(listResponse([]));

    renderPage("/curation/pending?page=3");

    await waitFor(() =>
      expect(mockGetJobs).toHaveBeenCalledWith({ page: 3, limit: 20 }),
    );
  });

  it("행 클릭이 ?page=를 달고 상세로 이동한다", async () => {
    // 3페이지짜리 목록이어야 page=3이 유지된다 — 범위를 넘는 page는 마지막 페이지로
    // 되접히는 것이 훅의 정상 동작이다(use-unconfirmed-jobs의 보정).
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [summary({ job_id: 11 })],
      pagination: { page: 3, limit: 20, total: 41, totalPages: 3 },
    });

    const router = renderPage("/curation/pending?page=3");

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "잡 #11 관측 상세" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "잡 #11 관측 상세" }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/curation/pending/11"),
    );
    expect(router.state.location.search).toBe("?page=3");
  });

  it("페이지네이션 클릭이 URL의 page를 바꿔 재조회를 일으킨다", async () => {
    // PaginationLink는 href 없는 <a>라 role="link"가 아니다 — 페이지 탐색 nav 안에서
    // 텍스트로 찾는다. 페이지네이션 블록은 totalPages > 1일 때만 렌더된다.
    mockGetJobs.mockResolvedValue({
      success: true,
      data: [summary({ job_id: 11 })],
      pagination: { page: 1, limit: 20, total: 40, totalPages: 2 },
    });

    const router = renderPage("/curation/pending");

    const nav = await screen.findByRole("navigation", { name: "페이지 탐색" });
    fireEvent.click(within(nav).getByText("2"));

    await waitFor(() => expect(router.state.location.search).toBe("?page=2"));
    expect(mockGetJobs).toHaveBeenCalledWith({ page: 2, limit: 20 });
  });
});
