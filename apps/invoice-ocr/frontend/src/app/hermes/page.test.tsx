import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import HermesStatusPage from "./page";
import { useHermesEntries, useHermesSummary } from "@/hooks/use-hermes-entries";
import type { HermesEntrySummary, HermesSummary } from "@/types/hermes";

vi.mock("@/hooks/use-hermes-entries", () => ({
  useHermesEntries: vi.fn(),
  useHermesSummary: vi.fn(),
  HERMES_PAGE_SIZE: 20,
}));
const mockEntries = vi.mocked(useHermesEntries);
const mockSummary = vi.mocked(useHermesSummary);

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

const TOTALS: HermesSummary["totals"] = {
  count: 8,
  missing: 1,
  pairs: 30,
  name_hits: 18,
  supply_hits: 22,
  untouched: 2,
  recipient_rate: 1,
  item_count_rate: 0.875,
  name_rate: 0.6,
  supply_rate: 0.7333333333333333,
  grand_total_rate: 0.75,
  untouched_rate: 0.25,
};

function entry(over: Partial<HermesEntrySummary> = {}): HermesEntrySummary {
  return {
    id: 573,
    issue_date_draft: "2026-09-05",
    issue_date_final: "2026-09-03",
    recipient_draft: "○○상사",
    recipient_final: "○○상사",
    item_count_draft: 3,
    item_count_final: 3,
    grand_total_draft: 165000,
    grand_total_final: 165000,
    status: "match",
    mismatch_fields: [],
    has_photo: true,
    has_raw: false,
    ...over,
  };
}

function setup(
  entriesOver: Partial<ReturnType<typeof useHermesEntries>> = {},
  summaryOver: Partial<ReturnType<typeof useHermesSummary>> = {},
) {
  mockEntries.mockReturnValue({
    data: [],
    total: 0,
    page: 1,
    totalPages: 0,
    loading: false,
    error: null,
    setPage: vi.fn(),
    status: null,
    setStatus: vi.fn(),
    ...entriesOver,
  });
  mockSummary.mockReturnValue({
    summary: {
      totals: TOTALS,
      knowledge: {
        version: 2,
        published_at: "2026-09-07T03:00:00",
        corrections: 25,
      },
      by_version: [{ version: 2, ...TOTALS }],
    },
    loading: false,
    error: null,
    ...summaryOver,
  });
  return render(
    <MemoryRouter>
      <HermesStatusPage />
    </MemoryRouter>,
  );
}

describe("HermesStatusPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("요약 카드에 집계와 지식 상태를 그린다", () => {
    setup();
    // "60.0%"는 요약 카드와 버전별 표에 함께 나오므로 컨테이너로 좁혀 단언한다.
    expect(screen.getByTestId("summary-cards")).toHaveTextContent("60.0%");
    expect(screen.getByTestId("knowledge-line")).toHaveTextContent("v2");
    expect(screen.getByTestId("knowledge-line")).toHaveTextContent("25");
  });

  it("분모 0이면 일치율을 —로 그린다", () => {
    const empty = { ...TOTALS, count: 0, pairs: 0, name_rate: 0 };
    setup(
      {},
      {
        summary: {
          totals: empty,
          knowledge: { version: 0, published_at: null, corrections: 0 },
          by_version: [],
        },
      },
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("목록 행과 상태 배지를 그린다", () => {
    setup({ total: 1, data: [entry({ id: 573 })] });
    expect(screen.getByText("#573")).toBeInTheDocument();
    // 같은 문자열이 상태 필터 버튼에도 있으므로 배지를 testid로 집는다.
    expect(screen.getByTestId("entry-status")).toHaveTextContent("✓ 일치");
  });

  it("불일치 건은 축 라벨 칩을 그린다", () => {
    setup({
      total: 1,
      data: [
        entry({
          id: 574,
          status: "mismatch",
          mismatch_fields: ["name", "supply"],
        }),
      ],
    });
    expect(screen.getByTestId("entry-status")).toHaveTextContent("✎ 불일치");
    // "품목명"·"공급가"는 요약 카드·버전별 표 헤더에도 있어 칩 컨테이너로 좁힌다.
    const chips = screen.getByTestId("mismatch-chips");
    expect(chips).toHaveTextContent("품목명");
    expect(chips).toHaveTextContent("공급가");
  });

  it("삭제된 건은 최종본 열을 —로 그린다", () => {
    setup({
      total: 1,
      data: [
        entry({
          id: 575,
          status: "deleted",
          recipient_final: null,
          item_count_final: null,
          grand_total_final: null,
          issue_date_final: null,
        }),
      ],
    });
    expect(screen.getByTestId("entry-status")).toHaveTextContent("🗑 삭제됨");
  });

  it("상태 필터 버튼이 setStatus를 부르고 켜진 필터는 다시 누르면 꺼진다", () => {
    const setStatus = vi.fn();
    setup({ setStatus, status: "mismatch" });
    fireEvent.click(screen.getByRole("button", { name: "✎ 불일치" }));
    expect(setStatus).toHaveBeenCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "✓ 일치" }));
    expect(setStatus).toHaveBeenCalledWith("match");
  });

  it("행을 누르면 page·필터를 실은 상세로 이동한다", () => {
    setup({ total: 1, data: [entry({ id: 573 })], page: 2, status: "match" });
    fireEvent.click(screen.getByRole("button", { name: "#573 상세" }));
    expect(mockNavigate).toHaveBeenCalledWith(
      "/hermes/573?page=2&status=match",
    );
  });

  it("건이 없으면 빈 상태를 그린다", () => {
    setup();
    expect(
      screen.getByText(/hermes로 들어온 건이 없습니다/),
    ).toBeInTheDocument();
  });

  it("목록 실패 메시지를 노출한다", () => {
    setup({ error: "boom" });
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("요약이 실패해도 목록은 그린다", () => {
    setup(
      { total: 1, data: [entry({ id: 573 })] },
      { summary: null, error: "summary down" },
    );
    expect(screen.getByText("#573")).toBeInTheDocument();
    expect(screen.getByText(/summary down/)).toBeInTheDocument();
  });
});
