import { renderHook, waitFor, act } from "@testing-library/react";
import { useCompanies } from "./use-companies";
import { companySuggestionsAPI } from "@/services/api";
import type { Company } from "@/types/company";

vi.mock("@/services/api", () => ({
  companySuggestionsAPI: {
    getList: vi.fn(),
  },
}));

const mockGetList = vi.mocked(companySuggestionsAPI.getList);

function createCompany(overrides: Partial<Company> = {}): Company {
  return { company_name: "테스트 거래처", ...overrides };
}

function createCompanyListResponse(data: Company[]) {
  return { data, total: data.length, page: 1, limit: 20 };
}

describe("useCompanies", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("거래처 목록을 조회한다", async () => {
    mockGetList.mockResolvedValue(
      createCompanyListResponse([
        createCompany({ id: 1, company_name: "한양운수" }),
      ]),
    );

    const { result } = renderHook(() => useCompanies());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("검색 쿼리를 전달한다", async () => {
    mockGetList.mockResolvedValue(createCompanyListResponse([]));

    renderHook(() => useCompanies("한양"));

    await waitFor(() => expect(mockGetList).toHaveBeenCalledWith("한양"));
  });

  it("쿼리 변경 시 재조회한다", async () => {
    mockGetList.mockResolvedValue(createCompanyListResponse([]));

    const { rerender } = renderHook(({ q }) => useCompanies(q), {
      initialProps: { q: "한양" },
    });

    await waitFor(() => expect(mockGetList).toHaveBeenCalledWith("한양"));

    rerender({ q: "대성" });

    await waitFor(() => expect(mockGetList).toHaveBeenCalledWith("대성"));
  });

  it("에러를 처리한다", async () => {
    mockGetList.mockRejectedValue(new Error("서버 오류"));

    const { result } = renderHook(() => useCompanies());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("서버 오류");
  });

  it("Error 인스턴스가 아닌 경우 기본 메시지를 사용한다", async () => {
    mockGetList.mockRejectedValue("unknown");

    const { result } = renderHook(() => useCompanies());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("거래처 목록을 불러올 수 없습니다");
  });

  it("refetch로 재조회한다", async () => {
    mockGetList.mockResolvedValue(createCompanyListResponse([]));

    const { result } = renderHook(() => useCompanies());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetList).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.refetch();
    });
    expect(mockGetList).toHaveBeenCalledTimes(2);
  });
});
