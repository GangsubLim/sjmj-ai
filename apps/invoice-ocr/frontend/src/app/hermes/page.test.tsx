import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import HermesStatusPage from "./page";
import { useHermesEntries, useHermesSummary } from "@/hooks/use-hermes-entries";
import { useHermesKnowledgeVersions } from "@/hooks/use-hermes-knowledge-versions";
import type {
  HermesEntrySummary,
  HermesKnowledgeVersion,
  HermesSummary,
} from "@/types/hermes";

vi.mock("@/hooks/use-hermes-entries", () => ({
  useHermesEntries: vi.fn(),
  useHermesSummary: vi.fn(),
  HERMES_PAGE_SIZE: 20,
}));
vi.mock("@/hooks/use-hermes-knowledge-versions", () => ({
  useHermesKnowledgeVersions: vi.fn(),
}));
const mockEntries = vi.mocked(useHermesEntries);
const mockSummary = vi.mocked(useHermesSummary);
const mockKnowledge = vi.mocked(useHermesKnowledgeVersions);

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

function knowledgeVersion(
  over: Partial<HermesKnowledgeVersion> = {},
): HermesKnowledgeVersion {
  return {
    version: 2,
    published_at: "2026-09-07T03:00:00",
    corrections_through: 25,
    rejected: null,
    changes: [],
    missing_file: false,
    ...over,
  };
}

function setup(
  entriesOver: Partial<ReturnType<typeof useHermesEntries>> = {},
  summaryOver: Partial<ReturnType<typeof useHermesSummary>> = {},
  knowledgeOver: Partial<ReturnType<typeof useHermesKnowledgeVersions>> = {},
) {
  mockKnowledge.mockReturnValue({
    versions: [knowledgeVersion()],
    loading: false,
    error: null,
    ...knowledgeOver,
  });
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
    refetch: vi.fn(),
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
    refetch: vi.fn(),
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
    // 전역 "—" 개수가 아니라 일치율 셀을 특정해 단언한다 — knowledge-line의
    // published_at ?? "—" 폴백이 만드는 "—"와 섞이면 formatRate 회귀를 못 잡는다.
    const summaryCards = screen.getByTestId("summary-cards");
    const nameCard = within(summaryCards).getByText("품목명").closest("div");
    const untouchedCard = within(summaryCards)
      .getByText("무수정률")
      .closest("div");
    if (!nameCard || !untouchedCard) {
      throw new Error("summary card not found");
    }
    expect(within(nameCard).getByText("—")).toBeInTheDocument();
    expect(within(untouchedCard).getByText("—")).toBeInTheDocument();
  });

  it("목록 행과 상태 배지를 그린다", () => {
    setup({ total: 1, data: [entry({ id: 573 })] });
    expect(screen.getByText("#573")).toBeInTheDocument();
    // 같은 문자열이 상태 필터 버튼에도 있으므로 배지를 testid로 집는다.
    expect(screen.getByTestId("entry-status")).toHaveTextContent("일치");
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
    expect(screen.getByTestId("entry-status")).toHaveTextContent("불일치");
    // "품목명"·"공급가"는 요약 카드·버전별 표 헤더에도 있어 칩 컨테이너로 좁힌다.
    const chips = screen.getByTestId("mismatch-chips");
    expect(chips).toHaveTextContent("품목명");
    expect(chips).toHaveTextContent("공급가");
  });

  it("서버가 match로 판정하면 공백 차이뿐인 수신처를 강조하지 않는다", () => {
    // recipient_draft·recipient_final이 원문 문자열로는 다르지만(공백), 서버 norm()
    // 정규화로 match라 mismatch_fields가 비어 있다 — 프론트가 문자열을 재비교해 강조를
    // 다시 그리면(회귀) 이 단언이 실패한다.
    setup({
      total: 1,
      data: [
        entry({
          id: 576,
          recipient_draft: "  ○○상사 ",
          recipient_final: "○○상사",
          mismatch_fields: [],
        }),
      ],
    });
    const row = screen.getByRole("button", { name: "#576 상세" }).closest("tr");
    if (!row) throw new Error("row not found");
    // 강조 클래스가 토큰(text-warning)으로 옮겨졌다 — 옛 amber를 찾으면 항상 통과한다.
    expect(row.innerHTML).not.toMatch(/text-warning|line-through/);
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
    expect(screen.getByTestId("entry-status")).toHaveTextContent("삭제됨");
    // 배지뿐 아니라 최종본 열 자체가 "초안값 → —"로 렌더되는지 행 스코프에서
    // 단언한다(DraftFinal의 final===null 분기 — page.tsx:283-286 회귀를 잡는다).
    const row = screen.getByRole("button", { name: "#575 상세" }).closest("tr");
    if (!row) throw new Error("row not found");
    const withinRow = within(row);
    expect(withinRow.getByText("○○상사 → —")).toBeInTheDocument();
    expect(withinRow.getByText("3 → —")).toBeInTheDocument();
    expect(withinRow.getByText("165,000 → —")).toBeInTheDocument();
  });

  it("상태 필터 버튼이 setStatus를 부르고 켜진 필터는 다시 누르면 꺼진다", () => {
    const setStatus = vi.fn();
    setup({ setStatus, status: "mismatch" });
    fireEvent.click(screen.getByRole("button", { name: "불일치" }));
    expect(setStatus).toHaveBeenCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "일치" }));
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

  it("목록 실패 메시지를 노출하고 다시 시도로 재조회한다", () => {
    const refetch = vi.fn();
    setup({ error: "boom", refetch });
    expect(screen.getByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("로딩 중에는 총계를 0건으로 단정하지 않는다", () => {
    setup({ loading: true, total: 0 });
    expect(screen.getByTestId("list-total")).toHaveTextContent("집계 중");
    expect(screen.getByTestId("list-total")).not.toHaveTextContent("0건");
  });

  it("필터가 켜지면 총계가 필터 스코프와 전체 건수를 함께 밝힌다", () => {
    // 요약 카드(전체 기준)와 목록 총계(필터 기준)가 나란히 놓여도 두 진실로 읽히지 않아야 한다.
    setup({ total: 2, status: "mismatch", data: [entry({ id: 573 })] });
    const label = screen.getByTestId("list-total");
    expect(label).toHaveTextContent("불일치 2건");
    expect(label).toHaveTextContent("전체 8건");
  });

  it("필터 때문에 빈 목록이면 필터를 끄는 버튼을 준다", () => {
    const setStatus = vi.fn();
    setup({ status: "deleted", setStatus });
    fireEvent.click(screen.getByRole("button", { name: "필터 끄기" }));
    expect(setStatus).toHaveBeenCalledWith(null);
  });

  it("페이지 번호는 키보드로 닿는 button이다", () => {
    // href 없는 <a>로 되돌아가면 2페이지 이후로 키보드·SR 진입이 불가능해진다.
    setup({ total: 40, totalPages: 2, data: [entry({ id: 573 })] });
    const nav = screen.getByRole("navigation", { name: "페이지 탐색" });
    expect(within(nav).getByRole("button", { name: "2" })).toBeInTheDocument();
  });

  it("요약이 실패해도 목록은 그린다", () => {
    setup(
      { total: 1, data: [entry({ id: 573 })] },
      { summary: null, error: "summary down" },
    );
    expect(screen.getByText("#573")).toBeInTheDocument();
    expect(screen.getByText(/summary down/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("summary down");
  });

  describe("지식 버전 표", () => {
    const CHANGES: HermesKnowledgeVersion["changes"] = [
      {
        section: "확정 어휘",
        added: [{ group: "보통(3~9회)", text: "콜드호수 (EA)" }],
        removed: [],
        moved: [{ text: "챔바 (EA)", from: "보통(3~9회)", to: "가끔(2회)" }],
        changed: [],
      },
      {
        section: "일반화 규칙",
        added: [],
        removed: [{ group: "", text: "옛 규칙" }],
        moved: [],
        changed: [
          {
            group: "",
            key: "합계를 다시 계산한다.",
            before: "(#580)",
            after: "(#580, #584)",
          },
        ],
      },
    ];

    it("전 발행 버전을 최신순으로 그리고 일치율은 by_version에 있는 버전만 채운다", () => {
      setup(
        {},
        {},
        {
          versions: [
            knowledgeVersion({
              version: 3,
              published_at: "2026-09-10T03:00:03",
            }),
            knowledgeVersion({ version: 2 }),
          ],
        },
      );
      const table = screen.getByTestId("version-table");
      const rows = within(table).getAllByRole("row").slice(1); // 헤더 제외
      expect(rows[0]).toHaveTextContent("v3");
      expect(rows[0]).toHaveTextContent("09-10");
      expect(rows[1]).toHaveTextContent("v2");
      // v3에는 초안이 없어 by_version에 없다 — 건수·일치율은 —.
      expect(within(rows[0]).getAllByText("—").length).toBeGreaterThanOrEqual(
        3,
      );
      expect(rows[1]).toHaveTextContent("60.0%");
    });

    it("발행 이전 구간은 맨 아래에 그린다", () => {
      setup(
        {},
        {
          summary: {
            totals: TOTALS,
            knowledge: { version: 2, published_at: null, corrections: 0 },
            by_version: [
              { version: 0, ...TOTALS },
              { version: 2, ...TOTALS },
            ],
          },
        },
      );
      const rows = within(screen.getByTestId("version-table"))
        .getAllByRole("row")
        .slice(1);
      expect(rows[0]).toHaveTextContent("v2");
      expect(rows[rows.length - 1]).toHaveTextContent("발행 이전");
    });

    it("변경 칩을 절별로 접어 그린다", () => {
      setup({}, {}, { versions: [knowledgeVersion({ changes: CHANGES })] });
      const chips = screen.getByTestId("knowledge-change-chips");
      expect(chips).toHaveTextContent("확정 어휘 +1 ↔1");
      expect(chips).toHaveTextContent("일반화 규칙 −1 ~1");
    });

    it("초기 발행·변경 없음·거부·파일 없음을 한 줄 설명으로 그린다", () => {
      setup(
        {},
        {},
        {
          versions: [
            knowledgeVersion({
              version: 4,
              rejected: "헤딩 누락",
              changes: null,
            }),
            knowledgeVersion({ version: 3, changes: null, missing_file: true }),
            knowledgeVersion({ version: 2, changes: [] }),
            knowledgeVersion({ version: 1, changes: null }),
          ],
        },
      );
      const table = screen.getByTestId("version-table");
      expect(table).toHaveTextContent("거부: 헤딩 누락");
      expect(table).toHaveTextContent("파일 없음");
      expect(table).toHaveTextContent("변경 없음");
      expect(table).toHaveTextContent("초기 발행");
      // 펼칠 것이 없는 행에는 펼침 버튼이 없다.
      expect(screen.queryByRole("button", { name: /변경 펼치기/ })).toBeNull();
    });

    it("행을 펼치면 추가·삭제·이동·변경 상세가 나온다", () => {
      setup({}, {}, { versions: [knowledgeVersion({ changes: CHANGES })] });
      expect(screen.queryByTestId("knowledge-change-detail")).toBeNull();
      const toggle = screen.getByRole("button", { name: "v2 변경 펼치기" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      const detail = screen.getByTestId("knowledge-change-detail");
      expect(detail).toHaveTextContent("보통(3~9회)");
      expect(detail).toHaveTextContent("콜드호수 (EA)");
      expect(detail).toHaveTextContent("챔바 (EA)");
      expect(detail).toHaveTextContent("보통(3~9회) → 가끔(2회)");
      expect(detail).toHaveTextContent("옛 규칙");
      expect(detail).toHaveTextContent("합계를 다시 계산한다.");
      expect(detail).toHaveTextContent("(#580) → (#580, #584)");
      fireEvent.click(toggle);
      expect(screen.queryByTestId("knowledge-change-detail")).toBeNull();
    });

    it("이력이 아직 안 왔어도 by_version 표는 먼저 그린다", () => {
      // 이력 API가 멈춰도(타임아웃 미설정) 기존 by_version 표가 무기한 가려지지 않는다.
      setup({}, {}, { versions: [], loading: true });
      const table = screen.getByTestId("version-table");
      expect(table).toHaveTextContent("v2");
      expect(table).toHaveTextContent("60.0%");
    });

    it("첫 발행 이전 거부 기록을 발행 이전 구간과 구분한다", () => {
      setup(
        {},
        {
          summary: {
            totals: TOTALS,
            knowledge: { version: 0, published_at: null, corrections: 0 },
            by_version: [{ version: 0, ...TOTALS }],
          },
        },
        {
          versions: [
            knowledgeVersion({
              version: 0,
              published_at: "2026-09-01T03:00:00",
              corrections_through: null,
              rejected: "헤딩 누락",
              changes: null,
            }),
          ],
        },
      );
      const rows = within(screen.getByTestId("version-table"))
        .getAllByRole("row")
        .slice(1);
      // 집계 구간 행 하나만 "발행 이전"이고, 거부 기록은 시도한 버전으로 선다.
      expect(
        rows.filter((r) => r.textContent?.includes("발행 이전")),
      ).toHaveLength(1);
      expect(rows[0]).toHaveTextContent("v1");
      expect(rows[0]).toHaveTextContent("거부: 헤딩 누락");
    });

    it("지식 버전 API가 실패해도 요약과 by_version 표는 그리고 오류를 알린다", () => {
      setup({}, {}, { versions: [], error: "knowledge down" });
      expect(screen.getByTestId("summary-cards")).toHaveTextContent("60.0%");
      expect(screen.getByText(/knowledge down/)).toBeInTheDocument();
      // 훅이 죽어도 by_version에 있는 v2 행은 일치율과 함께 남는다.
      const table = screen.getByTestId("version-table");
      expect(table).toHaveTextContent("v2");
      expect(table).toHaveTextContent("60.0%");
    });
  });
});
