import { renderHook } from "@testing-library/react";
import { useSettings } from "./use-settings";
import { useSettingsStore } from "@/stores/settings-store";

vi.mock("@/stores/settings-store", () => ({
  useSettingsStore: vi.fn(),
}));

const mockUseSettingsStore = vi.mocked(useSettingsStore);

describe("useSettings", () => {
  const baseMock = {
    issuer: null,
    appSettings: {
      default_vat_rate: 10,
      default_document_title: "거래명세서",
      pdf_filename_pattern: "{recipient}_{date}",
    },
    fetchIssuer: vi.fn(),
    updateIssuer: vi.fn(),
    fetchAppSettings: vi.fn(),
    updateAppSettings: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("isLoaded가 false이면 fetchIssuer와 fetchAppSettings를 호출한다", () => {
    mockUseSettingsStore.mockReturnValue({ ...baseMock, isLoaded: false });

    renderHook(() => useSettings());

    expect(baseMock.fetchIssuer).toHaveBeenCalledTimes(1);
    expect(baseMock.fetchAppSettings).toHaveBeenCalledTimes(1);
  });

  it("isLoaded가 true이면 fetch를 호출하지 않는다", () => {
    mockUseSettingsStore.mockReturnValue({ ...baseMock, isLoaded: true });

    renderHook(() => useSettings());

    expect(baseMock.fetchIssuer).not.toHaveBeenCalled();
    expect(baseMock.fetchAppSettings).not.toHaveBeenCalled();
  });

  it("store 전체를 반환한다", () => {
    const storeValue = { ...baseMock, isLoaded: true };
    mockUseSettingsStore.mockReturnValue(storeValue);

    const { result } = renderHook(() => useSettings());

    expect(result.current).toEqual(storeValue);
  });
});
