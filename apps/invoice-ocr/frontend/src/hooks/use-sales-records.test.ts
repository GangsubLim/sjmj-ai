import { renderHook, waitFor, act } from "@testing-library/react";
import { useSalesRecords } from "./use-sales-records";
import { salesRecordAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  salesRecordAPI: {
    getMonthly: vi.fn(),
    upsert: vi.fn(),
    remove: vi.fn(),
  },
}));

const mGet = vi.mocked(salesRecordAPI.getMonthly);
const mUpsert = vi.mocked(salesRecordAPI.upsert);

describe("useSalesRecords", () => {
  beforeEach(() => vi.clearAllMocks());

  it("flat array를 Map<date, Map<spId, record>>로 변환한다", async () => {
    mGet.mockResolvedValue({
      data: {
        salespeople: [{ id: 1, name: "A", sort_order: 0, is_active: 1 }],
        records: [
          {
            id: 10,
            salesperson_id: 1,
            work_date: "2026-05-15",
            quantity: 1000,
            snapshot_name: "A",
          },
          {
            id: 11,
            salesperson_id: 1,
            work_date: "2026-05-16",
            quantity: 2000,
            snapshot_name: "A",
          },
        ],
      },
    });

    const { result } = renderHook(() => useSalesRecords(2026, 5));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const m = result.current.recordsByDate;
    expect(m.get("2026-05-15")?.get(1)?.quantity).toBe(1000);
    expect(m.get("2026-05-16")?.get(1)?.quantity).toBe(2000);
  });

  it("year/month 변경 시 재조회", async () => {
    mGet.mockResolvedValue({ data: { salespeople: [], records: [] } });
    const { rerender } = renderHook(({ y, m }) => useSalesRecords(y, m), {
      initialProps: { y: 2026, m: 5 },
    });
    await waitFor(() => expect(mGet).toHaveBeenCalledWith(2026, 5));
    rerender({ y: 2026, m: 6 });
    await waitFor(() => expect(mGet).toHaveBeenCalledWith(2026, 6));
  });

  it("upsertRecord는 자동 refetch하지 않음, refetch 명시 호출 시 재조회", async () => {
    mGet.mockResolvedValue({ data: { salespeople: [], records: [] } });
    mUpsert.mockResolvedValue({
      data: {
        id: 1,
        salesperson_id: 1,
        work_date: "2026-05-15",
        quantity: 100,
        snapshot_name: "A",
      },
    });

    const { result } = renderHook(() => useSalesRecords(2026, 5));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.upsertRecord({
        salesperson_id: 1,
        work_date: "2026-05-15",
        quantity: 100,
      });
    });
    expect(mGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.refetch();
    });
    expect(mGet).toHaveBeenCalledTimes(2);
  });
});
