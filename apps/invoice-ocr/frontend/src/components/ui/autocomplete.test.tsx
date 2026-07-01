import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";

import { Autocomplete } from "./autocomplete";

// cmdk/radix-popover가 jsdom에서 마운트되려면 아래 브라우저 API가 필요하다.
beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
  }
});

const suggestions = [{ label: "무우", value: "1" }];

describe("Autocomplete onCommit", () => {
  it("타이핑 후 blur 시 onCommit을 입력값으로 1회 호출한다", () => {
    const onCommit = vi.fn();
    render(
      <Autocomplete
        value=""
        onChange={() => {}}
        onCommit={onCommit}
        suggestions={suggestions}
      />,
    );
    const input = screen.getByRole("textbox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "무" } });
    fireEvent.blur(input);

    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith("무");
  });

  it("항목 mousedown 선택 중 blur는 stale 입력값을 commit하지 않는다", () => {
    const onCommit = vi.fn();
    render(
      <Autocomplete
        value=""
        onChange={() => {}}
        onCommit={onCommit}
        suggestions={suggestions}
      />,
    );
    const input = screen.getByRole("textbox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "무" } }); // stale 입력값

    const item = screen.getByText("무우");
    fireEvent.mouseDown(item); // selectingRef set (blur보다 먼저)
    fireEvent.blur(input); // 가드: skip

    // 이중 발화/stale 선발 방지: blur가 "무"를 commit하면 안 된다.
    expect(onCommit).not.toHaveBeenCalledWith("무");
  });

  it("onCommit 미전달(기존 호출부)이면 blur가 no-op이며 throw하지 않는다", () => {
    render(
      <Autocomplete value="" onChange={() => {}} suggestions={suggestions} />,
    );
    const input = screen.getByRole("textbox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "무" } });

    expect(() => fireEvent.blur(input)).not.toThrow();
  });
});
