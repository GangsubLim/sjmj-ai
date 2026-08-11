import { renderHook, act, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { usePageParam, PAGE_MAX } from "./use-page-param";

// 현재 URL을 DOM으로 노출해 setPage의 부수효과를 단언 가능하게 만든다.
function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{location.pathname + location.search}</span>
  );
}

function renderPageParam(entry: string) {
  return renderHook(() => usePageParam(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={[entry]}>
        {children}
        <LocationProbe />
      </MemoryRouter>
    ),
  });
}

function currentUrl(): string {
  return screen.getByTestId("location").textContent ?? "";
}

describe("usePageParam", () => {
  it("쿼리가 없으면 1을 반환한다", () => {
    const { result } = renderPageParam("/curation");
    expect(result.current.page).toBe(1);
  });

  it("?page=3이면 3을 반환한다", () => {
    const { result } = renderPageParam("/curation?page=3");
    expect(result.current.page).toBe(3);
  });

  // Number.parseInt는 "3abc"·"3.5"를 3으로, "1e2"를 1로 받아들인다 — 문자열 전체를 검증해야 한다.
  it.each(["3abc", "3.5", "1e2", "0", "-1", "%203"])(
    "형식이 잘못된 ?page=%s는 1로 본다",
    (raw) => {
      const { result } = renderPageParam(`/curation?page=${raw}`);
      expect(result.current.page).toBe(1);
    },
  );

  it("?page=2000000000이면 PAGE_MAX로 clamp한다", () => {
    const { result } = renderPageParam("/curation?page=2000000000");
    expect(result.current.page).toBe(PAGE_MAX);
  });

  it("자릿수가 지나치게 긴 값도 PAGE_MAX로 clamp한다", () => {
    const { result } = renderPageParam(
      "/curation?page=99999999999999999999999",
    );
    expect(result.current.page).toBe(PAGE_MAX);
  });

  it("setPage(3)이 URL을 ?page=3으로 바꾼다", () => {
    const { result } = renderPageParam("/curation");
    act(() => result.current.setPage(3));
    expect(currentUrl()).toBe("/curation?page=3");
    expect(result.current.page).toBe(3);
  });

  it("setPage(1)이 page 키를 제거한다", () => {
    const { result } = renderPageParam("/curation?page=5");
    act(() => result.current.setPage(1));
    expect(currentUrl()).toBe("/curation");
    expect(result.current.page).toBe(1);
  });

  it("다른 쿼리 키를 보존한다", () => {
    const { result } = renderPageParam("/curation?tab=x&page=2");
    act(() => result.current.setPage(3));
    expect(currentUrl()).toBe("/curation?tab=x&page=3");
  });
});
