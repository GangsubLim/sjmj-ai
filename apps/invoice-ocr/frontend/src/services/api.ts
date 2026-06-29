import axios from "axios";
import type { Invoice, InvoiceFilters } from "@/types/invoice";
import type { Company } from "@/types/company";
import type { Item } from "@/types/item";
import { DEFAULT_APP_SETTINGS } from "@/types/settings";
import type { Issuer, AppSettings } from "@/types/settings";
import type { ListResponse, SingleResponse } from "@/types/api";
import type { Salesperson, SalespersonInput } from "@/types/salesperson";
import type {
  SalesRecord,
  MonthlySalesData,
  SalesRecordUpsertInput,
} from "@/types/sales-record";
import type { OcrJobStatus } from "@/types/ocr";

// --- Mock mode flag ---

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

// --- Environment & endpoint helpers ---

const API_MODE = (import.meta.env.VITE_API_MODE ?? "legacy") as
  | "legacy"
  | "modern";

const getApiBaseUrl = (): string => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL as string;
  }
  if (window.location.hostname === "kslim.dothome.co.kr") {
    return "https://kslim.dothome.co.kr/backend/api";
  }
  return "http://localhost:8000/api";
};

/** Returns endpoint path based on API_MODE */
const ep = (resource: string): string =>
  API_MODE === "legacy" ? `/${resource}.php` : `/${resource}`;

// 프론트 정렬 키 → FastAPI 정렬 화이트리스트(invoice_repository._ALLOWED_SORT_COLUMNS) 매핑.
const INVOICE_SORT_BY_MAP: Record<string, string> = {
  date: "issue_date",
  amount: "grand_total",
  company: "recipient",
};

const api = axios.create({
  baseURL: getApiBaseUrl(),
  headers: { "Content-Type": "application/json" },
});

// --- Real Invoice API ---

const _realInvoiceAPI = {
  getList: async (filters?: InvoiceFilters): Promise<ListResponse<Invoice>> => {
    const response = await api.get(ep("invoices"), {
      params: {
        page: filters?.page ?? 1,
        limit: filters?.limit ?? 20,
        search: filters?.search,
        date_from: filters?.date_from,
        date_to: filters?.date_to,
        sort_by: filters?.sort_by
          ? (INVOICE_SORT_BY_MAP[filters.sort_by] ?? filters.sort_by)
          : undefined,
        sort_order: filters?.sort_order,
      },
    });
    return response.data;
  },

  getById: async (id: number): Promise<SingleResponse<Invoice>> => {
    const response = await api.get(`${ep("invoices")}/${id}`);
    return response.data;
  },

  create: async (
    invoice: Omit<Invoice, "id" | "created_at" | "updated_at">,
  ): Promise<SingleResponse<Invoice>> => {
    const response = await api.post(ep("invoices"), invoice);
    return response.data;
  },

  update: async (
    id: number,
    invoice: Partial<Invoice>,
  ): Promise<SingleResponse<Invoice>> => {
    const response = await api.put(`${ep("invoices")}/${id}`, invoice);
    return response.data;
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    const response = await api.delete(`${ep("invoices")}/${id}`);
    return response.data;
  },

  duplicate: async (id: number): Promise<SingleResponse<Invoice>> => {
    if (API_MODE === "modern") {
      const response = await api.post(`${ep("invoices")}/${id}/duplicate`);
      return response.data;
    }
    // legacy fallback: fetch original, strip id, create new
    const original = await _realInvoiceAPI.getById(id);
    const {
      id: _id,
      created_at: _ca,
      updated_at: _ua,
      ...rest
    } = original.data;
    return _realInvoiceAPI.create(rest);
  },

  export: async (
    format: "csv" | "xlsx",
    filters?: InvoiceFilters,
  ): Promise<Blob> => {
    if (API_MODE !== "modern")
      throw new Error("Export는 modern API 모드에서만 사용 가능합니다");
    const response = await api.get(`${ep("invoices")}/export`, {
      params: { format, ...filters },
      responseType: "blob",
    });
    return response.data;
  },
};

// --- Real Company Suggestions API ---

const _realCompanySuggestionsAPI = {
  getList: async (query?: string): Promise<ListResponse<Company>> => {
    const response = await api.get(ep("companies"), {
      params: { q: query },
    });
    return response.data;
  },

  add: async (
    company: Omit<
      Company,
      "id" | "usage_count" | "last_used" | "created_at" | "updated_at"
    >,
  ): Promise<SingleResponse<Company>> => {
    const response = await api.post(ep("companies"), company);
    return response.data;
  },

  update: async (
    id: number,
    company: Partial<Company>,
  ): Promise<SingleResponse<Company>> => {
    if (API_MODE === "modern") {
      const response = await api.put(`${ep("companies")}/${id}`, company);
      return response.data;
    }
    // legacy fallback: delete + create (원자적 보장 불가; 실패 시 경고)
    const {
      id: _id,
      usage_count: _uc,
      last_used: _lu,
      created_at: _ca,
      updated_at: _ua,
      ...rest
    } = company as Company;
    await _realCompanySuggestionsAPI.delete(id);
    try {
      return await _realCompanySuggestionsAPI.add(rest);
    } catch (err) {
      console.warn(
        "[Legacy API] 거래처 업데이트 실패: delete 후 create 오류. 데이터가 손실되었을 수 있습니다.",
        err,
      );
      throw new Error("거래처 업데이트에 실패했습니다. 다시 시도해주세요.");
    }
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    const response = await api.delete(`${ep("companies")}/${id}`);
    return response.data;
  },
};

// --- Real Item Suggestions API ---

const _realItemSuggestionsAPI = {
  getList: async (
    query?: string,
    category?: string,
  ): Promise<ListResponse<Item>> => {
    const response = await api.get(ep("items"), {
      params: { q: query, category },
    });
    return response.data;
  },

  add: async (
    item: Omit<
      Item,
      "id" | "usage_count" | "last_used" | "created_at" | "updated_at"
    >,
  ): Promise<SingleResponse<Item>> => {
    const response = await api.post(ep("items"), item);
    return response.data;
  },

  update: async (
    id: number,
    item: Partial<Item>,
  ): Promise<SingleResponse<Item>> => {
    if (API_MODE === "modern") {
      const response = await api.put(`${ep("items")}/${id}`, item);
      return response.data;
    }
    // legacy fallback: delete + create (원자적 보장 불가; 실패 시 경고)
    const {
      id: _id,
      usage_count: _uc,
      last_used: _lu,
      created_at: _ca,
      updated_at: _ua,
      ...rest
    } = item as Item;
    await _realItemSuggestionsAPI.delete(id);
    try {
      return await _realItemSuggestionsAPI.add(rest);
    } catch (err) {
      console.warn(
        "[Legacy API] 품목 업데이트 실패: delete 후 create 오류. 데이터가 손실되었을 수 있습니다.",
        err,
      );
      throw new Error("품목 업데이트에 실패했습니다. 다시 시도해주세요.");
    }
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    const response = await api.delete(`${ep("items")}/${id}`);
    return response.data;
  },
};

// --- Real Settings API ---

const APP_SETTINGS_KEY = "sjmj_app_settings";

const _realSettingsAPI = {
  getIssuer: async (): Promise<SingleResponse<Issuer>> => {
    const endpoint =
      API_MODE === "modern" ? `${ep("settings")}/issuer` : ep("settings");
    const response = await api.get(endpoint);
    return response.data;
  },

  saveIssuer: async (issuer: Issuer): Promise<SingleResponse<Issuer>> => {
    if (API_MODE === "modern") {
      const response = await api.put(`${ep("settings")}/issuer`, issuer);
      return response.data;
    }
    const response = await api.post(ep("settings"), issuer);
    return response.data;
  },

  getAppSettings: async (): Promise<SingleResponse<AppSettings>> => {
    if (API_MODE === "modern") {
      // 서버는 app_settings를 문자열 key-value 맵으로 낸다(예: default_vat_rate:'0.1').
      // 프론트 AppSettings 형태(number vat·full shape)로 정규화한다.
      const response = await api.get(`${ep("settings")}/app`);
      const raw = (response.data?.data ?? {}) as Record<string, string>;
      const data: AppSettings = { ...DEFAULT_APP_SETTINGS };
      if (typeof raw.default_document_title === "string") {
        data.default_document_title = raw.default_document_title;
      }
      if (raw.default_vat_rate != null) {
        // 서버는 분수 문자열('0.1'), 프론트는 퍼센트 number(10) — 1 이하면 ×100.
        const n = Number(raw.default_vat_rate);
        if (!Number.isNaN(n)) {
          data.default_vat_rate = n <= 1 ? Math.round(n * 100) : n;
        }
      }
      return { data };
    }
    // legacy: localStorage fallback
    const stored = localStorage.getItem(APP_SETTINGS_KEY);
    let parsed: Partial<AppSettings> = {};
    try {
      if (stored) parsed = JSON.parse(stored);
    } catch {
      localStorage.removeItem(APP_SETTINGS_KEY);
    }
    const data = { ...DEFAULT_APP_SETTINGS, ...parsed };
    return { data };
  },

  updateAppSettings: async (
    settings: Partial<AppSettings>,
  ): Promise<SingleResponse<AppSettings>> => {
    if (API_MODE === "modern") {
      // 프론트 형태 → 서버 문자열 맵(vat: 퍼센트 number → 분수 문자열)으로 변환 후 전송.
      const payload: Record<string, string> = {};
      if (settings.default_document_title != null) {
        payload.default_document_title = settings.default_document_title;
      }
      if (settings.default_vat_rate != null) {
        payload.default_vat_rate = String(settings.default_vat_rate / 100);
      }
      await api.put(`${ep("settings")}/app`, payload);
      // 서버가 갱신된 맵을 돌려주지만, 정규화 일관성을 위해 getAppSettings로 재조회한다.
      return _realSettingsAPI.getAppSettings();
    }
    // legacy: localStorage fallback
    const current = await _realSettingsAPI.getAppSettings();
    const merged = { ...current.data, ...settings };
    localStorage.setItem(APP_SETTINGS_KEY, JSON.stringify(merged));
    return { data: merged };
  },
};

// --- Real Salesperson API ---

const _realSalespersonAPI = {
  getList: async (): Promise<ListResponse<Salesperson>> => {
    const response = await api.get("/salespeople");
    return response.data;
  },

  create: async (
    input: SalespersonInput,
  ): Promise<SingleResponse<Salesperson>> => {
    const response = await api.post("/salespeople", input);
    return response.data;
  },

  update: async (
    id: number,
    input: Partial<Salesperson>,
  ): Promise<SingleResponse<Salesperson>> => {
    const response = await api.put(`/salespeople/${id}`, input);
    return response.data;
  },

  remove: async (id: number): Promise<SingleResponse<null>> => {
    const response = await api.delete(`/salespeople/${id}`);
    return response.data;
  },
};

// --- Real Sales Record API ---

const _realSalesRecordAPI = {
  getMonthly: async (
    year: number,
    month: number,
  ): Promise<SingleResponse<MonthlySalesData>> => {
    const response = await api.get("/sales-records", {
      params: { year, month },
    });
    return response.data;
  },

  upsert: async (
    input: SalesRecordUpsertInput,
  ): Promise<SingleResponse<SalesRecord>> => {
    const response = await api.post("/sales-records", input);
    return response.data;
  },

  remove: async (id: number): Promise<SingleResponse<null>> => {
    const response = await api.delete(`/sales-records/${id}`);
    return response.data;
  },
};

// --- Real OCR API ---

const _realOcrAPI = {
  createJob: async (file: File) => {
    const form = new FormData();
    form.append("photo", file);
    const response = await api.post("/ocr/jobs", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data as {
      success: boolean;
      data: { job_id: number; status: string };
    };
  },

  getJob: async (id: number) => {
    const response = await api.get(`/ocr/jobs/${id}`);
    return response.data as { success: boolean; data: OcrJobStatus };
  },

  confirm: async (id: number, payload: unknown) => {
    const response = await api.post(`/ocr/jobs/${id}/confirm`, payload);
    return response.data as { success: boolean; data: { invoice_id: number } };
  },
};

export const ocrAPI = _realOcrAPI;

// --- Conditional exports: mock or real API ---

async function loadMockAPIs() {
  const mock = await import("@/mocks/api");
  return mock;
}

let _mockPromise: ReturnType<typeof loadMockAPIs> | null = null;
const getMock = () => {
  if (!_mockPromise) _mockPromise = loadMockAPIs();
  return _mockPromise;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function createMockProxy<T extends Record<string, (...args: any[]) => any>>(
  real: T,
  getMockImpl: () => Promise<T>,
): T {
  return new Proxy(real, {
    get(_target, prop: string) {
      if (!USE_MOCK) return real[prop as keyof T];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return async (...args: any[]) => {
        const mock = await getMockImpl();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        return (mock[prop as keyof T] as (...a: any[]) => any)(...args);
      };
    },
  }) as T;
}

export const invoiceAPI = createMockProxy(
  _realInvoiceAPI,
  async () => (await getMock()).mockInvoiceAPI as typeof _realInvoiceAPI,
);

export const companySuggestionsAPI = createMockProxy(
  _realCompanySuggestionsAPI,
  async () =>
    (await getMock())
      .mockCompanySuggestionsAPI as typeof _realCompanySuggestionsAPI,
);

export const itemSuggestionsAPI = createMockProxy(
  _realItemSuggestionsAPI,
  async () =>
    (await getMock()).mockItemSuggestionsAPI as typeof _realItemSuggestionsAPI,
);

export const settingsAPI = createMockProxy(
  _realSettingsAPI,
  async () => (await getMock()).mockSettingsAPI as typeof _realSettingsAPI,
);

export const salespersonAPI = createMockProxy(
  _realSalespersonAPI,
  async () =>
    (await getMock()).mockSalespersonAPI as typeof _realSalespersonAPI,
);

export const salesRecordAPI = createMockProxy(
  _realSalesRecordAPI,
  async () =>
    (await getMock()).mockSalesRecordAPI as typeof _realSalesRecordAPI,
);

export default api;
