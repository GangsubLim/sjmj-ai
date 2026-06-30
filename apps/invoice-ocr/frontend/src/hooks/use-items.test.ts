import { renderHook, waitFor, act } from "@testing-library/react";
import { useItems } from "./use-items";
import { itemSuggestionsAPI } from "@/services/api";
import type { Item } from "@/types/item";

vi.mock("@/services/api", () => ({
  itemSuggestionsAPI: {
    getList: vi.fn(),
  },
}));

const mockGetList = vi.mocked(itemSuggestionsAPI.getList);

function createItem(overrides: Partial<Item> = {}): Item {
  return { item_name: "테스트 품목", ...overrides };
}

function createItemListResponse(data: Item[]) {
  return {
    data,
    pagination: { total: data.length, page: 1, limit: 20, totalPages: 1 },
  };
}

describe("useItems", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("품목 목록을 조회한다", async () => {
    mockGetList.mockResolvedValue(
      createItemListResponse([createItem({ id: 1, item_name: "엔진오일" })]),
    );

    const { result } = renderHook(() => useItems());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("쿼리와 카테고리를 전달한다", async () => {
    mockGetList.mockResolvedValue(createItemListResponse([]));

    renderHook(() => useItems("오일", "oil"));

    await waitFor(() =>
      expect(mockGetList).toHaveBeenCalledWith("오일", "oil"),
    );
  });

  it("파라미터 변경 시 재조회한다", async () => {
    mockGetList.mockResolvedValue(createItemListResponse([]));

    const { rerender } = renderHook(({ q, cat }) => useItems(q, cat), {
      initialProps: { q: "오일", cat: "oil" },
    });

    await waitFor(() =>
      expect(mockGetList).toHaveBeenCalledWith("오일", "oil"),
    );

    rerender({ q: "타이어", cat: "tires" });

    await waitFor(() =>
      expect(mockGetList).toHaveBeenCalledWith("타이어", "tires"),
    );
  });

  it("에러를 처리한다", async () => {
    mockGetList.mockRejectedValue(new Error("조회 실패"));

    const { result } = renderHook(() => useItems());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("조회 실패");
  });

  it("Error 인스턴스가 아닌 경우 기본 메시지를 사용한다", async () => {
    mockGetList.mockRejectedValue(42);

    const { result } = renderHook(() => useItems());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("품목 목록을 불러올 수 없습니다");
  });

  it("refetch로 재조회한다", async () => {
    mockGetList.mockResolvedValue(createItemListResponse([]));

    const { result } = renderHook(() => useItems());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetList).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.refetch();
    });
    expect(mockGetList).toHaveBeenCalledTimes(2);
  });
});
