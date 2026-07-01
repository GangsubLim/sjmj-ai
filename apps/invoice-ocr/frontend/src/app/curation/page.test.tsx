import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import CurationQueuePage from "./page";
import { useCurationJobs } from "@/hooks/use-curation-jobs";

vi.mock("@/hooks/use-curation-jobs", () => ({ useCurationJobs: vi.fn() }));
const mockHook = vi.mocked(useCurationJobs);

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

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

  it("검수된 잡은 검수됨 배지를 렌더한다", () => {
    setup({
      total: 1,
      data: [
        {
          job_id: 200,
          invoice_id: 50,
          curation_reviewed: true,
          pair_count: 3,
          unreviewed_count: 0,
          created_at: "2026-06-29T09:00:00",
        },
      ],
    });
    expect(screen.getByText("✓ 검수됨")).toBeInTheDocument();
  });

  it("빈 큐는 EmptyState를 보여준다", () => {
    setup();
    expect(screen.getByText("검수할 잡이 없습니다")).toBeInTheDocument();
  });

  function setupSingleJob() {
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
    return screen.getByRole("button", { name: "잡 #128 상세" });
  }

  it("행 클릭 시 상세로 네비게이트한다", () => {
    const row = setupSingleJob();
    fireEvent.click(row);
    expect(mockNavigate).toHaveBeenCalledWith("/curation/128");
  });

  it("각 잡 행을 시맨틱 row로 노출하고 접근성 버튼을 제공한다", () => {
    setupSingleJob();
    expect(screen.getByRole("row", { name: /128/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "잡 #128 상세" }),
    ).toBeInTheDocument();
  });
});
