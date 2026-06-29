import { renderHook, waitFor, act } from "@testing-library/react";
import { useSalespeople } from "./use-salespeople";
import { salespersonAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  salespersonAPI: {
    getList: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
}));

const mGetList = vi.mocked(salespersonAPI.getList);
const mCreate = vi.mocked(salespersonAPI.create);
const mRemove = vi.mocked(salespersonAPI.remove);

describe("useSalespeople", () => {
  beforeEach(() => vi.clearAllMocks());

  it("초기 로드 시 목록 조회", async () => {
    mGetList.mockResolvedValue({
      data: [{ id: 1, name: "A", sort_order: 0, is_active: 1 }],
      total: 1,
      page: 1,
      limit: 1,
    });
    const { result } = renderHook(() => useSalespeople());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
  });

  it("create 후 refetch", async () => {
    mGetList.mockResolvedValue({ data: [], total: 0, page: 1, limit: 0 });
    mCreate.mockResolvedValue({
      data: { id: 2, name: "B", sort_order: 0, is_active: 1 },
    });

    const { result } = renderHook(() => useSalespeople());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.create({ name: "B", sort_order: 0 });
    });
    expect(mCreate).toHaveBeenCalled();
    expect(mGetList).toHaveBeenCalledTimes(2);
  });

  it("softDelete 후 refetch", async () => {
    mGetList.mockResolvedValue({ data: [], total: 0, page: 1, limit: 0 });
    mRemove.mockResolvedValue({ data: null });

    const { result } = renderHook(() => useSalespeople());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.softDelete(3);
    });
    expect(mRemove).toHaveBeenCalledWith(3);
    expect(mGetList).toHaveBeenCalledTimes(2);
  });

  it("에러 처리", async () => {
    mGetList.mockRejectedValue(new Error("조회 실패"));
    const { result } = renderHook(() => useSalespeople());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("조회 실패");
  });
});
