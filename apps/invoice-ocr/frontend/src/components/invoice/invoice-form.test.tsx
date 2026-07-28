import { render, screen, fireEvent, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { InvoiceForm } from "./invoice-form";
import { useCompanies } from "@/hooks/use-companies";
import { useItems } from "@/hooks/use-items";
import { useAddNewItem } from "@/hooks/use-add-new-item";
import { useSettings } from "@/hooks/use-settings";
import { DEFAULT_APP_SETTINGS } from "@/types/settings";
import type { Item } from "@/types/item";

// cmdk/radix-popover가 jsdom에서 마운트되려면 아래 브라우저 API가 필요하다
// (invoice-item-row.test.tsx와 동일 셋업).
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

vi.mock("@/hooks/use-companies", () => ({ useCompanies: vi.fn() }));
vi.mock("@/hooks/use-items", () => ({ useItems: vi.fn() }));
vi.mock("@/hooks/use-add-new-item", () => ({ useAddNewItem: vi.fn() }));
vi.mock("@/hooks/use-settings", () => ({ useSettings: vi.fn() }));
vi.mock("@/hooks/use-media-query", () => ({ useMediaQuery: () => false }));

const mockUseCompanies = vi.mocked(useCompanies);
const mockUseItems = vi.mocked(useItems);
const mockUseAddNewItem = vi.mocked(useAddNewItem);
const mockUseSettings = vi.mocked(useSettings);

function setup() {
  mockUseCompanies.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    refetch: vi.fn(),
  });
  mockUseItems.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    refetch: vi.fn(),
  });
  mockUseSettings.mockReturnValue({
    issuer: null,
    appSettings: DEFAULT_APP_SETTINGS,
    isLoaded: true,
    fetchIssuer: vi.fn(),
    updateIssuer: vi.fn(),
    fetchAppSettings: vi.fn(),
    updateAppSettings: vi.fn(),
  });
  return render(
    <MemoryRouter>
      <InvoiceForm mode="create" />
    </MemoryRouter>,
  );
}

// 품목 행 컨테이너를 이름 aria-label로 스코프해서 찾는다(다른 행의 팝오버와 섞이지 않도록).
function rowByNameLabel(label: string): HTMLElement {
  const input = screen.getByLabelText(label);
  const row = input.closest('[data-slot="invoice-item-row"]');
  if (!row) throw new Error(`행을 찾을 수 없습니다: ${label}`);
  return row as HTMLElement;
}

describe("InvoiceForm 신규 품목 등록 배선", () => {
  beforeEach(() => vi.clearAllMocks());

  it("새로 추가 클릭 시 addNewItem을 호출하고, 백엔드가 default_unit_price:0을 돌려줘도 행의 기존 단가를 보존한다", async () => {
    const addNewItem = vi.fn<(name: string) => Promise<Item | null>>();
    addNewItem.mockResolvedValue({
      id: 9,
      item_name: "신품목",
      default_unit_price: 0,
    });
    mockUseAddNewItem.mockReturnValue(addNewItem);
    setup();

    const row = rowByNameLabel("품목 1 이름");
    fireEvent.change(within(row).getByLabelText("단가"), {
      target: { value: "45000" },
    });
    fireEvent.change(within(row).getByLabelText("품목 1 이름"), {
      target: { value: "신품목" },
    });

    await act(async () => {
      // Popover 콘텐츠는 Radix Portal로 body에 렌더되므로 row 스코프가 아닌
      // 전역에서, 입력값까지 포함한 정확한 문구로 찾는다.
      fireEvent.click(screen.getByText('"신품목" 새로 추가'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(addNewItem).toHaveBeenCalledWith("신품목");
    const updatedRow = rowByNameLabel("품목 1 이름");
    expect(within(updatedRow).getByLabelText("품목 1 이름")).toHaveValue(
      "신품목",
    );
    expect(within(updatedRow).getByLabelText("단가")).toHaveValue("45,000");
  });

  it("응답 대기 중 대상 행이 삭제되면(index 아닌 _tempId로 식별) 남은 행이 오염되지 않는다", async () => {
    let resolveAdd: (item: Item) => void = () => {};
    const pending = new Promise<Item>((resolve) => {
      resolveAdd = resolve;
    });
    const addNewItem = vi.fn<(name: string) => Promise<Item | null>>();
    addNewItem.mockReturnValue(pending);
    mockUseAddNewItem.mockReturnValue(addNewItem);
    setup();

    // 두 번째 행 추가
    fireEvent.click(screen.getByText("품목 추가"));

    const row2 = rowByNameLabel("품목 2 이름");
    fireEvent.change(within(row2).getByLabelText("품목 2 이름"), {
      target: { value: "기존행" },
    });

    const row1 = rowByNameLabel("품목 1 이름");
    fireEvent.change(within(row1).getByLabelText("품목 1 이름"), {
      target: { value: "듣보1" },
    });
    // Popover 콘텐츠는 Radix Portal로 body에 렌더된다. row2 팝오버도 열려 있을 수
    // 있으므로 입력값까지 포함한 정확한 문구로 row1의 항목만 특정한다.
    fireEvent.click(screen.getByText('"듣보1" 새로 추가'));
    expect(addNewItem).toHaveBeenCalledWith("듣보1");

    // 응답이 오기 전에 첫 번째 행을 삭제 — 남은 행이 index 0이 된다
    fireEvent.click(screen.getByLabelText("품목 1 삭제"));

    await act(async () => {
      resolveAdd({ id: 9, item_name: "듣보1", default_unit_price: 0 });
      await pending;
      await Promise.resolve();
    });

    // index 캡처였다면 이제 index 0인 "기존행"이 "듣보1"로 덮어써진다 — 그러면 안 된다
    expect(screen.getByLabelText("품목 1 이름")).toHaveValue("기존행");
  });
});
