import { render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import HermesEntryPage from "./page";
import { useHermesEntry } from "@/hooks/use-hermes-entry";
import type { HermesEntryDetail, HermesRow } from "@/types/hermes";

vi.mock("@/hooks/use-hermes-entry", () => ({ useHermesEntry: vi.fn() }));
const mockHook = vi.mocked(useHermesEntry);

function cell(over: Partial<NonNullable<HermesRow["draft"]>> = {}) {
  return {
    name: "히타",
    quantity: 1,
    unit: "EA",
    unit_price: 150000,
    supply: 150000,
    deduction: false,
    ...over,
  };
}

function detail(over: Partial<HermesEntryDetail> = {}): HermesEntryDetail {
  return {
    id: 573,
    status: "mismatch",
    draft: {
      issue_date: "2026-09-05",
      recipient: "○○상사",
      grand_total: 165000,
    },
    final: {
      id: 573,
      issue_date: "2026-09-03",
      recipient: "○○상사",
      grand_total: 165000,
    },
    rows: [
      {
        index: 0,
        raw: { text: "히타", conf: "중" },
        draft: cell(),
        final: cell({ name: "히터" }),
        mismatch: ["name"],
      },
    ],
    has_photo: true,
    // 기본 픽스처는 품목명만 갈린 실제 판정과 같은 축을 싣는다 — 수신처·합계는 일치.
    mismatch_fields: ["name"],
    ...over,
  };
}

function setup(
  over: Partial<ReturnType<typeof useHermesEntry>> = {},
  url = "/hermes/573",
) {
  mockHook.mockReturnValue({
    entry: detail(),
    loading: false,
    error: null,
    ...over,
  });
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/hermes/:id" element={<HermesEntryPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("HermesEntryPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("URL의 id로 상세를 조회한다", () => {
    setup();
    expect(mockHook).toHaveBeenCalledWith(573);
  });

  it("전사값·초안·최종본 3단을 그린다", () => {
    setup();
    expect(screen.getByText("전사값")).toBeInTheDocument();
    expect(screen.getByText("중")).toBeInTheDocument(); // 확신 등급 배지
    // "히타"는 전사값(raw.text)과 초안(draft.name) 두 열에 각각 따로 렌더돼야 한다 —
    // 하나만 나와도 통과하는 length>0은 두 열이 실제로 그려졌는지 재지 못한다.
    expect(screen.getAllByText("히타").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("히터")).toBeInTheDocument();
  });

  it("raw가 전건 null이면 전사값 열 자체를 숨긴다", () => {
    setup({
      entry: detail({
        rows: [
          {
            index: 0,
            raw: null,
            draft: cell(),
            final: cell({ name: "히터" }),
            mismatch: ["name"],
          },
        ],
      }),
    });
    expect(screen.queryByText("전사값")).not.toBeInTheDocument();
  });

  it("발행일은 강조 없이 초안→최종으로만 표시한다", () => {
    setup();
    const issued = screen.getByTestId("issue-date-compare");
    expect(issued).toHaveTextContent("2026-09-05");
    expect(issued).toHaveTextContent("2026-09-03");
    // 강조 클래스가 붙으면 사실상 전건이 빨개진다(spec §3.2). 자기 자신의 className뿐
    // 아니라 내부에 강조용 자식 요소를 심어도 걸리도록 innerHTML까지 함께 검사한다.
    expect(issued.className).not.toMatch(/amber|destructive/);
    expect(issued.innerHTML).not.toMatch(/amber|destructive/);
  });

  it("서버가 match로 판정하면 공백 차이뿐인 수신처를 강조하지 않는다", () => {
    // draft.recipient·final.recipient가 원문 문자열로는 다르지만(공백), 서버 norm()
    // 정규화로 match라 mismatch_fields에 recipient가 없다 — 프론트가 문자열을 재비교해
    // 강조를 다시 그리면(회귀) 이 단언이 실패한다.
    setup({
      entry: detail({
        mismatch_fields: [],
        draft: {
          issue_date: "2026-09-05",
          recipient: "  ○○상사 ",
          grand_total: 165000,
        },
        final: {
          id: 573,
          issue_date: "2026-09-03",
          recipient: "○○상사",
          grand_total: 165000,
        },
      }),
    });
    const recipientLine = screen.getByText("수신처").closest("p");
    if (!recipientLine) throw new Error("recipient line not found");
    expect(recipientLine.innerHTML).not.toMatch(/amber|line-through/);
  });

  it("사람이 추가한 행은 초안 칸을 —로 그린다", () => {
    setup({
      entry: detail({
        rows: [
          {
            index: 0,
            raw: null,
            draft: null,
            final: cell({ name: "용접봉" }),
            mismatch: [],
          },
        ],
      }),
    });
    const finalCell = screen.getByText("용접봉");
    const row = finalCell.closest("tr");
    expect(row).not.toBeNull();
    // 같은 행 안에서 초안 칸(짝 없음)만 —여야 한다 — 전역 getAllByText("—")는
    // 다른 행·다른 열의 —와 뒤섞여 이 행의 초안 칸을 특정하지 못한다.
    expect(within(row as HTMLElement).getByText("—")).toBeInTheDocument();
  });

  it("삭제된 건은 최종본 없음을 알린다", () => {
    setup({
      entry: detail({
        status: "deleted",
        final: null,
        rows: [
          { index: 0, raw: null, draft: cell(), final: null, mismatch: [] },
        ],
      }),
    });
    expect(screen.getByText("🗑 삭제됨")).toBeInTheDocument();
    // 상태 배지만으로는 "이 건의 최종본이 실제로 비어 있다"를 재지 못한다 —
    // 해당 행의 최종본 칸이 —로 그려지는지까지 확인한다.
    const draftCell = screen.getByText("히타");
    const row = draftCell.closest("tr") as HTMLElement;
    expect(within(row).getByText("—")).toBeInTheDocument();
  });

  it("교정 링크와 목록 복귀 링크가 page·필터를 보존한다", () => {
    setup({}, "/hermes/573?page=2&status=mismatch");
    expect(screen.getByRole("link", { name: /교정/ })).toHaveAttribute(
      "href",
      "/edit/573",
    );
    expect(screen.getByRole("link", { name: /목록/ })).toHaveAttribute(
      "href",
      "/hermes?page=2&status=mismatch",
    );
  });

  it("실패 메시지를 노출한다", () => {
    setup({ entry: null, error: "없는 건" });
    expect(screen.getByText("없는 건")).toBeInTheDocument();
  });
});
