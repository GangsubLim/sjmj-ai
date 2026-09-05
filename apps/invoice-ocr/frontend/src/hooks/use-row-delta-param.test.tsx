import { renderHook, act } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { useRowDeltaParam } from "./use-row-delta-param";

function renderParam(entry: string) {
  return renderHook(
    () => ({ ...useRowDeltaParam(), location: useLocation() }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
      ),
    },
  );
}

describe("useRowDeltaParam", () => {
  it("URL의 row_delta=true를 읽는다", () => {
    const { result } = renderParam("/curation?row_delta=true");
    expect(result.current.rowDelta).toBe(true);
  });

  it("파라미터가 없으면 꺼짐이다", () => {
    const { result } = renderParam("/curation");
    expect(result.current.rowDelta).toBe(false);
  });

  it("켤 때 page를 함께 버린다", () => {
    // 필터를 켜면 결과 집합이 좁아져 옛 page가 범위 밖이 된다 — 그대로 두면 빈 목록이 뜬다.
    const { result } = renderParam("/curation?page=5");
    act(() => result.current.setRowDelta(true));
    expect(result.current.location.search).toBe("?row_delta=true");
  });

  it("끄면 파라미터가 사라진다", () => {
    const { result } = renderParam("/curation?page=5&row_delta=true");
    act(() => result.current.setRowDelta(false));
    expect(result.current.location.search).toBe("");
  });
});
