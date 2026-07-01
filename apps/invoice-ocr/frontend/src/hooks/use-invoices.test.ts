import { renderHook, waitFor, act } from "@testing-library/react";
import { useInvoices, useInvoice } from "./use-invoices";
import { invoiceAPI } from "@/services/api";
import type { Invoice } from "@/types/invoice";
import type { ListResponse, SingleResponse } from "@/types/api";

vi.mock("@/services/api", () => ({
  invoiceAPI: {
    getList: vi.fn(),
    getById: vi.fn(),
  },
}));

const mockGetList = vi.mocked(invoiceAPI.getList);
const mockGetById = vi.mocked(invoiceAPI.getById);

function createInvoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    document_title: "거래명세서",
    issue_date: "2026-01-01",
    recipient: "테스트",
    vehicle_no: "",
    show_stamp: false,
    total_supply: 0,
    total_vat: 0,
    grand_total: 0,
    items: [],
    ...overrides,
  };
}

function createListResponse(
  data: Invoice[],
  total?: number,
): ListResponse<Invoice> {
  return {
    data,
    pagination: {
      page: 1,
      limit: 20,
      total: total ?? data.length,
      totalPages: 1,
    },
  };
}

function createSingleResponse(
  invoice: Partial<Invoice> = {},
): SingleResponse<Invoice> {
  return { data: createInvoice(invoice) };
}

describe("useInvoices", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("목록을 조회한다", async () => {
    const invoice = createInvoice({ id: 1 });
    mockGetList.mockResolvedValue(createListResponse([invoice], 1));

    const { result } = renderHook(() => useInvoices());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.total).toBe(1);
    expect(result.current.error).toBeNull();
  });

  it("필터를 전달한다", async () => {
    mockGetList.mockResolvedValue(createListResponse([]));

    const filters = { search: "한양", sort_by: "date" as const };
    renderHook(() => useInvoices(filters));

    await waitFor(() => expect(mockGetList).toHaveBeenCalledWith(filters));
  });

  it("에러를 처리한다", async () => {
    mockGetList.mockRejectedValue(new Error("네트워크 오류"));

    const { result } = renderHook(() => useInvoices());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("네트워크 오류");
    expect(result.current.data).toEqual([]);
  });

  it("Error 인스턴스가 아닌 경우 기본 메시지를 사용한다", async () => {
    mockGetList.mockRejectedValue("unknown");

    const { result } = renderHook(() => useInvoices());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("거래명세서 목록을 불러올 수 없습니다");
  });
});

describe("useInvoice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("ID로 단건 조회한다", async () => {
    mockGetById.mockResolvedValue(createSingleResponse({ id: 1 }));

    const { result } = renderHook(() => useInvoice(1));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(createInvoice({ id: 1 }));
  });

  it("id가 undefined이면 조회하지 않는다", () => {
    const { result } = renderHook(() => useInvoice(undefined));

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
    expect(mockGetById).not.toHaveBeenCalled();
  });

  it("refetch로 재조회한다", async () => {
    mockGetById.mockResolvedValue(createSingleResponse({ id: 1 }));

    const { result } = renderHook(() => useInvoice(1));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetById).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.refetch();
    });
    expect(mockGetById).toHaveBeenCalledTimes(2);
  });
});
