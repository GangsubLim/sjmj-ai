import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";

import { InvoiceItemRow } from "./invoice-item-row";
import type { InvoiceItem } from "@/types/invoice";

// cmdk/radix-popover가 jsdom에서 마운트되려면 아래 브라우저 API가 필요하다
// (CurationPairRow.test.tsx와 동일 셋업).
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

const ITEM: InvoiceItem = {
  name: "",
  quantity: 1,
  unit_price: 0,
  supply: 0,
  vat: 0,
  total: 0,
  item_order: 0,
  deduction: false,
};

describe("InvoiceItemRow", () => {
  it("검색 결과가 0건이면 신규 품목 등록 항목이 뜨고, 선택 시 입력값을 넘긴다", () => {
    const onAddNewItem = vi.fn();
    render(
      <InvoiceItemRow
        item={ITEM}
        index={0}
        itemSuggestions={[{ label: "엔진오일", value: "1" }]}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
        onAddNewItem={onAddNewItem}
      />,
    );

    const input = screen.getByLabelText("품목 1 이름");
    fireEvent.change(input, { target: { value: "듣보품목" } });
    fireEvent.click(screen.getByText(/새로 추가/));

    expect(onAddNewItem).toHaveBeenCalledWith("듣보품목");
  });

  it("onAddNewItem이 없으면 '결과 없음'만 뜬다(호출자가 prop을 넘기지 않는 경우의 방어 동작)", () => {
    render(
      <InvoiceItemRow
        item={ITEM}
        index={0}
        itemSuggestions={[{ label: "엔진오일", value: "1" }]}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("품목 1 이름"), {
      target: { value: "듣보품목" },
    });
    expect(screen.getByText("결과 없음")).toBeInTheDocument();
    expect(screen.queryByText(/새로 추가/)).not.toBeInTheDocument();
  });
});
