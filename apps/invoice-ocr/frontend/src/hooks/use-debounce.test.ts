import { renderHook, act } from "@testing-library/react";
import { useDebounce } from "./use-debounce";

describe("useDebounce", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("초기값을 즉시 반환한다", () => {
    const { result } = renderHook(() => useDebounce("hello"));
    expect(result.current).toBe("hello");
  });

  it("딜레이 전에는 이전 값을 유지한다", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: "a" } },
    );

    rerender({ value: "b" });
    expect(result.current).toBe("a");
  });

  it("딜레이 후에 새 값을 반환한다", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: "a" } },
    );

    rerender({ value: "b" });
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current).toBe("b");
  });

  it("연속 변경 시 타이머를 리셋한다", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: "a" } },
    );

    rerender({ value: "b" });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    rerender({ value: "c" });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    // 아직 500ms가 안 지남 (b 이후 300 + c 이후 300, c의 타이머는 200 남음)
    expect(result.current).toBe("a");

    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe("c");
  });

  it("기본 딜레이는 300ms이다", () => {
    const { result, rerender } = renderHook(({ value }) => useDebounce(value), {
      initialProps: { value: "x" },
    });

    rerender({ value: "y" });
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe("x");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe("y");
  });
});
