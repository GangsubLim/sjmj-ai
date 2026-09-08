import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useHermesEntry } from "./use-hermes-entry";
import { hermesAPI } from "@/services/api";
import type { HermesEntryDetail } from "@/types/hermes";

vi.mock("@/services/api", () => ({ hermesAPI: { getEntry: vi.fn() } }));
const mockGetEntry = vi.mocked(hermesAPI.getEntry);

const DETAIL: HermesEntryDetail = {
  id: 573,
  status: "mismatch",
  draft: { issue_date: "2026-09-05", recipient: "○○상사", grand_total: 165000 },
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
      draft: {
        name: "히타",
        quantity: 1,
        unit: "EA",
        unit_price: 150000,
        supply: 150000,
        deduction: false,
      },
      final: {
        name: "히터",
        quantity: 1,
        unit: "EA",
        unit_price: 150000,
        supply: 150000,
        deduction: false,
      },
      mismatch: ["name"],
    },
  ],
  has_photo: true,
};

describe("useHermesEntry", () => {
  beforeEach(() => vi.clearAllMocks());

  it("상세를 불러온다", async () => {
    mockGetEntry.mockResolvedValue({ data: DETAIL });
    const { result } = renderHook(() => useHermesEntry(573));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.entry?.rows[0].mismatch).toEqual(["name"]);
    expect(mockGetEntry).toHaveBeenCalledWith(573);
  });

  it("id가 없으면 요청하지 않는다", async () => {
    const { result } = renderHook(() => useHermesEntry(undefined));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetEntry).not.toHaveBeenCalled();
    expect(result.current.entry).toBeNull();
  });

  it("실패 메시지를 노출한다", async () => {
    mockGetEntry.mockRejectedValue(new Error("없는 건"));
    const { result } = renderHook(() => useHermesEntry(1));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("없는 건");
  });
});
