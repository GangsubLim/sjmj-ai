import { renderHook, act } from "@testing-library/react";
import { useSalesInput } from "./use-sales-input";

describe("useSalesInput", () => {
  it("초기값 prefill", () => {
    const initial = new Map<number, { id: number; quantity: number }>([
      [1, { id: 10, quantity: 1000 }],
    ]);
    const { result } = renderHook(() => useSalesInput(initial, [1, 2]));
    expect(result.current.values.get(1)).toBe("1000");
    expect(result.current.values.get(2)).toBe("");
  });

  it("값 변경 시 dirty 표시", () => {
    const { result } = renderHook(() => useSalesInput(new Map(), [1]));
    act(() => result.current.setValue(1, "5000"));
    expect(result.current.values.get(1)).toBe("5000");
    expect(result.current.isDirty(1)).toBe(true);
  });

  it("clear (empty) → DELETE 의도", () => {
    const initial = new Map<number, { id: number; quantity: number }>([
      [1, { id: 10, quantity: 1000 }],
    ]);
    const { result } = renderHook(() => useSalesInput(initial, [1]));
    act(() => result.current.setValue(1, ""));
    const mut = result.current.getMutations();
    expect(mut.deletes).toEqual([10]);
    expect(mut.upserts).toHaveLength(0);
  });

  it("'0' 명시 입력 → UPSERT 0", () => {
    const { result } = renderHook(() => useSalesInput(new Map(), [1]));
    act(() => result.current.setValue(1, "0"));
    const mut = result.current.getMutations();
    expect(mut.upserts).toEqual([{ salesperson_id: 1, quantity: 0 }]);
    expect(mut.deletes).toHaveLength(0);
  });

  it("미변경 행은 mutation 없음", () => {
    const initial = new Map<number, { id: number; quantity: number }>([
      [1, { id: 10, quantity: 1000 }],
    ]);
    const { result } = renderHook(() => useSalesInput(initial, [1]));
    const mut = result.current.getMutations();
    expect(mut.upserts).toHaveLength(0);
    expect(mut.deletes).toHaveLength(0);
  });

  it("동일 값으로 set 해도 dirty 아님", () => {
    const initial = new Map<number, { id: number; quantity: number }>([
      [1, { id: 10, quantity: 1000 }],
    ]);
    const { result } = renderHook(() => useSalesInput(initial, [1]));
    act(() => result.current.setValue(1, "1000"));
    expect(result.current.isDirty(1)).toBe(false);
  });

  it("천단위 콤마는 raw integer만 추출", () => {
    const { result } = renderHook(() => useSalesInput(new Map(), [1]));
    act(() => result.current.setValue(1, "1,234,567"));
    const mut = result.current.getMutations();
    expect(mut.upserts).toEqual([{ salesperson_id: 1, quantity: 1234567 }]);
  });
});
