import { settingsAPI } from "@/services/api";
import { useSettingsStore } from "./settings-store";
import type { Issuer, AppSettings } from "@/types/settings";
import type { SingleResponse } from "@/types/api";

vi.mock("@/services/api", () => ({
  settingsAPI: {
    getIssuer: vi.fn(),
    saveIssuer: vi.fn(),
    getAppSettings: vi.fn(),
    updateAppSettings: vi.fn(),
  },
}));

const mockSettingsAPI = vi.mocked(settingsAPI);

const DEFAULT_APP_SETTINGS: AppSettings = {
  default_vat_rate: 10,
  default_document_title: "거래명세서",
  pdf_filename_pattern: "{recipient}_{date}",
};

function createIssuer(overrides: Partial<Issuer> = {}): Issuer {
  return {
    company_name: "SJMJ",
    representative: "홍길동",
    business_number: "1234567890",
    address: "서울시",
    business_type: "서비스",
    business_item: "자동차정비",
    phone: "02-1234-5678",
    fax: "02-1234-5679",
    bank_account: "국민 123-456-789",
    tel_fax: "02-1234-5678",
    show_sjdojang: false,
    ...overrides,
  };
}

function createIssuerResponse(
  overrides: Partial<Issuer> = {},
): SingleResponse<Issuer> {
  return { data: createIssuer(overrides) };
}

function createAppSettingsResponse(
  overrides: Partial<AppSettings> = {},
): SingleResponse<AppSettings> {
  return { data: { ...DEFAULT_APP_SETTINGS, ...overrides } };
}

describe("useSettingsStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSettingsStore.setState({
      issuer: null,
      appSettings: { ...DEFAULT_APP_SETTINGS },
      isLoaded: false,
    });
  });

  it("기본 상태가 올바르다", () => {
    const state = useSettingsStore.getState();
    expect(state.issuer).toBeNull();
    expect(state.isLoaded).toBe(false);
    expect(state.appSettings.default_vat_rate).toBe(10);
  });

  it("fetchIssuer가 issuer를 설정하고 isLoaded를 true로 만든다", async () => {
    mockSettingsAPI.getIssuer.mockResolvedValue(
      createIssuerResponse({ id: 1 }),
    );

    await useSettingsStore.getState().fetchIssuer();

    const state = useSettingsStore.getState();
    expect(state.issuer).toEqual(createIssuer({ id: 1 }));
    expect(state.isLoaded).toBe(true);
  });

  it("fetchAppSettings가 appSettings를 설정한다", async () => {
    const settings: AppSettings = {
      default_vat_rate: 5,
      default_document_title: "세금계산서",
      pdf_filename_pattern: "{date}_{recipient}",
    };
    mockSettingsAPI.getAppSettings.mockResolvedValue({ data: settings });

    await useSettingsStore.getState().fetchAppSettings();

    expect(useSettingsStore.getState().appSettings).toEqual(settings);
  });

  it("updateIssuer가 issuer를 갱신한다", async () => {
    const updated = createIssuer({ id: 1, company_name: "SJMJ 수정" });
    mockSettingsAPI.saveIssuer.mockResolvedValue({ data: updated });

    await useSettingsStore.getState().updateIssuer(updated);

    expect(useSettingsStore.getState().issuer).toEqual(updated);
  });

  it("updateAppSettings가 현재 설정과 병합하여 업데이트한다", async () => {
    const merged: AppSettings = {
      default_vat_rate: 10,
      default_document_title: "견적서",
      pdf_filename_pattern: "{recipient}_{date}",
    };
    mockSettingsAPI.updateAppSettings.mockResolvedValue(
      createAppSettingsResponse({ default_document_title: "견적서" }),
    );

    await useSettingsStore
      .getState()
      .updateAppSettings({ default_document_title: "견적서" });

    expect(mockSettingsAPI.updateAppSettings).toHaveBeenCalledWith(merged);
    expect(useSettingsStore.getState().appSettings).toEqual(merged);
  });

  it("fetchIssuer가 실패하면 에러를 삼키고 isLoaded를 true로 설정한다", async () => {
    mockSettingsAPI.getIssuer.mockRejectedValue(new Error("서버 오류"));

    await useSettingsStore.getState().fetchIssuer(); // should NOT throw

    expect(useSettingsStore.getState().issuer).toBeNull();
    expect(useSettingsStore.getState().isLoaded).toBe(true);
  });

  it("updateAppSettings가 실패하면 상태를 변경하지 않는다", async () => {
    mockSettingsAPI.updateAppSettings.mockRejectedValue(
      new Error("업데이트 실패"),
    );

    await expect(
      useSettingsStore
        .getState()
        .updateAppSettings({ default_document_title: "견적서" }),
    ).rejects.toThrow("업데이트 실패");
    expect(useSettingsStore.getState().appSettings).toEqual(
      DEFAULT_APP_SETTINGS,
    );
  });
});
