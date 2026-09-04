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
    rowDelta: false,
    setRowDelta: vi.fn(),
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
          curation_reviewed_at: null,
          pair_count: 7,
          unreviewed_count: 7,
          rows_added: null,
          rows_dropped: null,
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
          curation_reviewed_at: "2026-06-29T09:00:00",
          pair_count: 3,
          unreviewed_count: 0,
          rows_added: null,
          rows_dropped: null,
          created_at: "2026-06-29T09:00:00",
        },
      ],
    });
    expect(screen.getByText("✓ 검수됨")).toBeInTheDocument();
  });

  it("재검수 필요 상태의 잡은 ↺ 뱃지를 렌더한다", () => {
    setup({
      total: 1,
      data: [
        {
          job_id: 300,
          invoice_id: 60,
          curation_reviewed: false,
          // 검수됐다가 쌍 수정으로 해제된 잡 — "미검수"와 구분돼야 한다(AC 3).
          curation_reviewed_at: "2026-06-29T09:00:00",
          pair_count: 3,
          unreviewed_count: 1,
          rows_added: null,
          rows_dropped: null,
          created_at: "2026-06-29T09:00:00",
        },
      ],
    });
    expect(screen.getByText("↺ 재검수 필요")).toBeInTheDocument();
    expect(screen.queryByText("● 미검수")).not.toBeInTheDocument();
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
          curation_reviewed_at: null,
          pair_count: 7,
          unreviewed_count: 7,
          rows_added: null,
          rows_dropped: null,
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

  it("3페이지에서 행을 클릭하면 ?page=3을 달고 상세로 이동한다", () => {
    setup({
      page: 3,
      total: 1,
      data: [
        {
          job_id: 128,
          invoice_id: 341,
          curation_reviewed: false,
          curation_reviewed_at: null,
          pair_count: 7,
          unreviewed_count: 7,
          rows_added: null,
          rows_dropped: null,
          created_at: "2026-06-30T09:00:00",
        },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "잡 #128 상세" }));
    expect(mockNavigate).toHaveBeenCalledWith("/curation/128?page=3");
  });

  it("행 증감 컬럼을 방향별로 분리해 렌더한다", () => {
    setup({
      total: 1,
      data: [
        {
          job_id: 128,
          invoice_id: 341,
          curation_reviewed: false,
          curation_reviewed_at: null,
          pair_count: 7,
          unreviewed_count: 7,
          rows_added: 2,
          rows_dropped: 1,
          created_at: "2026-06-30T09:00:00",
        },
      ],
    });
    expect(screen.getByText("행 증감")).toBeInTheDocument();
    expect(screen.getByText("+2 / −1")).toBeInTheDocument();
  });

  it("필터 토글이 현재 상태를 aria-pressed로 알리고 setRowDelta를 부른다", () => {
    const setRowDelta = vi.fn();
    setup({ rowDelta: false, setRowDelta });

    const toggle = screen.getByRole("button", { name: "행 증감만" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);

    expect(setRowDelta).toHaveBeenCalledWith(true);
  });

  it("필터가 켜진 빈 목록은 필터 기준으로 문구를 바꾼다", () => {
    setup({ total: 0, data: [], rowDelta: true });
    expect(
      screen.getByText("행 증감이 관측된 잡이 없습니다"),
    ).toBeInTheDocument();
  });
});
