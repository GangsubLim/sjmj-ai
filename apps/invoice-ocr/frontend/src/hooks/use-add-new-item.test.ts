import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAddNewItem } from "./use-add-new-item";
import { itemSuggestionsAPI } from "@/services/api";
import { toast } from "sonner";

vi.mock("@/services/api", () => ({ itemSuggestionsAPI: { add: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const mockAdd = vi.mocked(itemSuggestionsAPI.add);

beforeEach(() => vi.clearAllMocks());

describe("useAddNewItem", () => {
  it("품목 DB에 등록하고 생성된 항목을 돌려준다", async () => {
    mockAdd.mockResolvedValue({
      success: true,
      data: { id: 9, item_name: "신품목" },
    });
    const { result } = renderHook(() => useAddNewItem());

    let created: unknown;
    await act(async () => {
      created = await result.current("신품목");
    });

    expect(mockAdd).toHaveBeenCalledWith({ item_name: "신품목" });
    expect(created).toMatchObject({ id: 9, item_name: "신품목" });
    expect(vi.mocked(toast.success)).toHaveBeenCalled();
  });

  it("실패하면 null을 돌려주고 토스트로 알린다(입력값은 호출자가 유지한다)", async () => {
    mockAdd.mockRejectedValue(new Error("중복된 이름입니다"));
    const { result } = renderHook(() => useAddNewItem());

    let created: unknown = "sentinel";
    await act(async () => {
      created = await result.current("신품목");
    });

    expect(created).toBeNull();
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith("중복된 이름입니다");
  });
});
