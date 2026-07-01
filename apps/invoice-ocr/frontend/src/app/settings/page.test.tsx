import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

import SettingsPage from "./page";
import { useSettings } from "@/hooks/use-settings";
import { useSalespeople } from "@/hooks/use-salespeople";
import { DEFAULT_APP_SETTINGS } from "@/types/settings";
import type { Issuer } from "@/types/settings";

vi.mock("@/hooks/use-settings", () => ({ useSettings: vi.fn() }));
vi.mock("@/hooks/use-salespeople", () => ({ useSalespeople: vi.fn() }));

// jsdom은 matchMedia 미구현. SalespeopleSection이 useMediaQuery로 호출하므로 폴리필.
beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
});

const mockUseSettings = vi.mocked(useSettings);
const mockUseSalespeople = vi.mocked(useSalespeople);

const ISSUER: Issuer = {
  company_name: "회사",
  representative: "대표",
  business_number: "",
  address: "",
  business_type: "",
  business_item: "",
  phone: "",
  fax: "",
  bank_account: "",
  tel_fax: "",
  show_sjdojang: true,
};

function setup() {
  mockUseSettings.mockReturnValue({
    issuer: ISSUER,
    appSettings: DEFAULT_APP_SETTINGS,
    isLoaded: true,
    fetchIssuer: vi.fn(),
    updateIssuer: vi.fn(),
    fetchAppSettings: vi.fn(),
    updateAppSettings: vi.fn(),
  });
  mockUseSalespeople.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    refetch: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    softDelete: vi.fn(),
  });
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  );
}

describe("SettingsPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("하단에 OCR 학습 큐레이션 보조 링크를 /curation으로 렌더한다", () => {
    setup();
    const link = screen.getByRole("link", { name: "OCR 학습 큐레이션" });
    expect(link).toHaveAttribute("href", "/curation");
  });

  it("큐레이션 링크는 데스크톱 전용(hidden lg:block) 래퍼 안에 있다", () => {
    setup();
    const link = screen.getByRole("link", { name: "OCR 학습 큐레이션" });
    expect(link.parentElement?.className ?? "").toMatch(/hidden/);
    expect(link.parentElement?.className ?? "").toMatch(/lg:block/);
  });
});
