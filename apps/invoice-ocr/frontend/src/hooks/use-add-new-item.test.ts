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

  it("이름 앞뒤 공백은 제거하고 전송한다(대소문자만 무시하는 중복검사망을 피해가는 공백 중복 방지)", async () => {
    mockAdd.mockResolvedValue({
      success: true,
      data: { id: 10, item_name: "엔진오일" },
    });
    const { result } = renderHook(() => useAddNewItem());

    await act(async () => {
      await result.current("  엔진오일  ");
    });

    expect(mockAdd).toHaveBeenCalledWith({ item_name: "엔진오일" });
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

  it("axios 에러이고 서버 envelope에 message가 있으면 그 메시지를 그대로 보여준다(DUPLICATE_NAME 등)", async () => {
    const axiosLikeError = {
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          error: {
            code: "DUPLICATE_NAME",
            message: "이미 등록된 품목명입니다",
          },
        },
      },
    };
    mockAdd.mockRejectedValue(axiosLikeError);
    const { result } = renderHook(() => useAddNewItem());

    let created: unknown = "sentinel";
    await act(async () => {
      created = await result.current("신품목");
    });

    expect(created).toBeNull();
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      "이미 등록된 품목명입니다",
    );
  });

  it("Error가 아닌 값이 던져지면 기본 메시지로 폴백한다", async () => {
    mockAdd.mockRejectedValue("문자열 throw");
    const { result } = renderHook(() => useAddNewItem());

    let created: unknown = "sentinel";
    await act(async () => {
      created = await result.current("신품목");
    });

    expect(created).toBeNull();
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      "품목 등록에 실패했습니다",
    );
  });
});
