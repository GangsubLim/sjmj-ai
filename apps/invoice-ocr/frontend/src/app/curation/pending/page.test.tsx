import { render, screen, waitFor } from "@testing-library/react";
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

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/curation/pending", element: <UnconfirmedJobsPage /> },
      { path: "/curation/:jobId", element: <div>확정 후 상세</div> },
    ],
    { initialEntries: ["/curation/pending"] },
  );
  return render(<RouterProvider router={router} />);
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
});
