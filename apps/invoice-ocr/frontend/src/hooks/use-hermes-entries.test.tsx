import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { useHermesEntries, useHermesSummary } from "./use-hermes-entries";
import { hermesAPI } from "@/services/api";
import type { HermesEntrySummary, HermesSummary } from "@/types/hermes";

vi.mock("@/services/api", () => ({
  hermesAPI: { getEntries: vi.fn(), getSummary: vi.fn() },
}));

const mockGetEntries = vi.mocked(hermesAPI.getEntries);
const mockGetSummary = vi.mocked(hermesAPI.getSummary);

function entry(over: Partial<HermesEntrySummary> = {}): HermesEntrySummary {
  return {
    id: 573,
    issue_date_draft: "2026-09-05",
    issue_date_final: "2026-09-05",
    recipient_draft: "○○상사",
    recipient_final: "○○상사",
    item_count_draft: 1,
    item_count_final: 1,
    grand_total_draft: 165000,
    grand_total_final: 165000,
    status: "match",
    mismatch_fields: [],
    has_photo: true,
    has_raw: false,
    ...over,
  };
}

function listResponse(data: HermesEntrySummary[], total = data.length) {
  return { data, pagination: { page: 1, limit: 20, total, totalPages: 1 } };
}

const SUMMARY: HermesSummary = {
  totals: {
    count: 9,
    missing: 1,
    pairs: 30,
    name_hits: 18,
    supply_hits: 22,
    untouched: 2,
    recipient_rate: 1,
    item_count_rate: 0.9,
    name_rate: 0.6,
    supply_rate: 0.74,
    grand_total_rate: 0.8,
    untouched_rate: 0.22,
  },
  knowledge: {
    version: 2,
    published_at: "2026-09-07T03:00:00",
    corrections: 25,
  },
  by_version: [],
};

function renderEntries(entryUrl = "/hermes") {
  return renderHook(() => useHermesEntries(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={[entryUrl]}>{children}</MemoryRouter>
    ),
  });
}

describe("useHermesEntries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetEntries.mockResolvedValue(listResponse([entry()], 9));
  });

  it("목록과 total을 노출한다", async () => {
    const { result } = renderEntries();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.total).toBe(9);
  });

  it("URL의 status를 읽어 요청에 싣는다", async () => {
    const { result } = renderEntries("/hermes?status=mismatch");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.status).toBe("mismatch");
    expect(mockGetEntries).toHaveBeenCalledWith(
      expect.objectContaining({ status: "mismatch" }),
    );
  });

  it("필터가 없으면 status 키 자체를 넘기지 않는다", async () => {
    const { result } = renderEntries();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetEntries.mock.calls[0][0]).not.toHaveProperty("status");
  });

  it("URL의 미지 status는 필터 없음으로 접는다", async () => {
    const { result } = renderEntries("/hermes?status=UNKNOWN");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.status).toBeNull();
    expect(mockGetEntries.mock.calls[0][0]).not.toHaveProperty("status");
  });

  it("필터를 바꾸면 1페이지로 되돌린다", async () => {
    const { result } = renderEntries("/hermes?page=3");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.page).toBe(3);
    act(() => result.current.setStatus("deleted"));
    await waitFor(() => expect(result.current.page).toBe(1));
    expect(result.current.status).toBe("deleted");
  });

  it("실패 메시지를 삼키지 않고 노출한다", async () => {
    mockGetEntries.mockRejectedValue(new Error("boom"));
    const { result } = renderEntries();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("boom");
  });
});

describe("useHermesSummary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSummary.mockResolvedValue({ data: SUMMARY });
  });

  it("요약을 한 번만 불러온다", async () => {
    const { result } = renderHook(() => useHermesSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.summary?.knowledge.version).toBe(2);
    expect(mockGetSummary).toHaveBeenCalledTimes(1);
  });

  it("실패해도 요약만 null로 두고 메시지를 노출한다", async () => {
    mockGetSummary.mockRejectedValue(new Error("summary down"));
    const { result } = renderHook(() => useHermesSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.summary).toBeNull();
    expect(result.current.error).toBe("summary down");
  });
});
