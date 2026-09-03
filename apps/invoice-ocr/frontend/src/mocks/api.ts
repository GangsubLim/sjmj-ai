/**
 * Mock API - VITE_USE_MOCK=true 일 때 실제 API 대신 사용
 * 인메모리 데이터로 CRUD를 시뮬레이션합니다.
 */
import type { Invoice, InvoiceFilters } from "@/types/invoice";
import type { Company } from "@/types/company";
import type { Item } from "@/types/item";
import type { Issuer, AppSettings } from "@/types/settings";
import type { ListResponse, SingleResponse } from "@/types/api";
import type { Salesperson } from "@/types/salesperson";
import type {
  SalesRecord,
  MonthlySalesData,
  SalesRecordUpsertInput,
} from "@/types/sales-record";
import type {
  CurationJobSummary,
  CurationJobDetail,
  CurationPairPatchBody,
  CurationPairPatchResult,
} from "@/types/curation";
import { mockInvoices } from "./invoices";
import { mockCompanies } from "./companies";
import { mockItems } from "./items";
import { mockIssuer, mockAppSettings } from "./settings";
import { mockSalespeople } from "./salespeople";
import { mockSalesRecords } from "./sales-records";
import { mockCurationJobDetails } from "./curation";
import { formatYYYYMMDD } from "@/utils/calendar";

// --- In-memory stores (deep clone to avoid mutation of originals) ---
let invoices: Invoice[] = JSON.parse(JSON.stringify(mockInvoices));
let companies: Company[] = JSON.parse(JSON.stringify(mockCompanies));
let items: Item[] = JSON.parse(JSON.stringify(mockItems));
let issuer: Issuer = JSON.parse(JSON.stringify(mockIssuer));
let appSettings: AppSettings = JSON.parse(JSON.stringify(mockAppSettings));

let nextInvoiceId = Math.max(0, ...invoices.map((i) => i.id ?? 0)) + 1;
let nextCompanyId = Math.max(0, ...companies.map((c) => c.id ?? 0)) + 1;
let nextItemId = Math.max(0, ...items.map((i) => i.id ?? 0)) + 1;
let salespeople: Salesperson[] = JSON.parse(JSON.stringify(mockSalespeople));
let salesRecords: SalesRecord[] = JSON.parse(JSON.stringify(mockSalesRecords));
let nextSalespersonId = Math.max(0, ...salespeople.map((s) => s.id ?? 0)) + 1;
let nextSalesRecordId = Math.max(0, ...salesRecords.map((s) => s.id ?? 0)) + 1;

const delay = (ms = 200) => new Promise((r) => setTimeout(r, ms));

// --- Invoice API ---

export const mockInvoiceAPI = {
  getList: async (filters?: InvoiceFilters): Promise<ListResponse<Invoice>> => {
    await delay();
    let list = [...invoices];

    if (filters?.search) {
      const q = filters.search.toLowerCase();
      list = list.filter(
        (inv) =>
          inv.recipient.toLowerCase().includes(q) ||
          inv.vehicle_no.toLowerCase().includes(q),
      );
    }

    if (filters?.date_from) {
      list = list.filter((inv) => inv.issue_date >= filters.date_from!);
    }
    if (filters?.date_to) {
      list = list.filter((inv) => inv.issue_date <= filters.date_to!);
    }

    if (filters?.sort_by) {
      list.sort((a, b) => {
        const order = filters.sort_order === "asc" ? 1 : -1;
        switch (filters.sort_by) {
          case "date":
            return order * a.issue_date.localeCompare(b.issue_date);
          case "amount":
            return order * (a.grand_total - b.grand_total);
          case "company":
            return order * a.recipient.localeCompare(b.recipient);
          default:
            return 0;
        }
      });
    } else {
      list.sort((a, b) => b.issue_date.localeCompare(a.issue_date));
    }

    const total = list.length;
    const page = filters?.page ?? 1;
    const limit = filters?.limit ?? 20;
    const start = (page - 1) * limit;
    const paged = list.slice(start, start + limit);

    return {
      data: paged,
      pagination: { page, limit, total, totalPages: Math.ceil(total / limit) },
    };
  },

  getById: async (id: number): Promise<SingleResponse<Invoice>> => {
    await delay();
    const found = invoices.find((inv) => inv.id === id);
    if (!found) throw new Error("거래명세서를 찾을 수 없습니다");
    return { data: { ...found, items: found.items.map((i) => ({ ...i })) } };
  },

  create: async (
    invoice: Omit<Invoice, "id" | "created_at" | "updated_at">,
  ): Promise<SingleResponse<Invoice>> => {
    await delay();
    const now = new Date().toISOString();
    const created: Invoice = {
      ...invoice,
      id: nextInvoiceId++,
      created_at: now,
      updated_at: now,
    };
    invoices.unshift(created);
    return { data: created, message: "저장되었습니다" };
  },

  update: async (
    id: number,
    invoice: Partial<Invoice>,
  ): Promise<SingleResponse<Invoice>> => {
    await delay();
    const idx = invoices.findIndex((inv) => inv.id === id);
    if (idx === -1) throw new Error("거래명세서를 찾을 수 없습니다");
    invoices[idx] = {
      ...invoices[idx],
      ...invoice,
      updated_at: new Date().toISOString(),
    };
    return { data: invoices[idx], message: "수정되었습니다" };
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    await delay();
    invoices = invoices.filter((inv) => inv.id !== id);
    return { data: null, message: "삭제되었습니다" };
  },

  duplicate: async (id: number): Promise<SingleResponse<Invoice>> => {
    await delay();
    const original = invoices.find((inv) => inv.id === id);
    if (!original) throw new Error("거래명세서를 찾을 수 없습니다");
    const now = new Date().toISOString();
    const duplicated: Invoice = {
      ...JSON.parse(JSON.stringify(original)),
      id: nextInvoiceId++,
      issue_date: new Date().toISOString().slice(0, 10),
      created_at: now,
      updated_at: now,
    };
    invoices.unshift(duplicated);
    return { data: duplicated, message: "복제되었습니다" };
  },

  export: async (
    _format?: "csv" | "xlsx",
    _filters?: InvoiceFilters,
  ): Promise<Blob | null> => null,
};

// --- Company Suggestions API ---

export const mockCompanySuggestionsAPI = {
  getList: async (query?: string): Promise<ListResponse<Company>> => {
    await delay();
    let list = [...companies];
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((c) => c.company_name.toLowerCase().includes(q));
    }
    list.sort((a, b) => (b.usage_count ?? 0) - (a.usage_count ?? 0));
    return {
      data: list,
      pagination: {
        page: 1,
        limit: list.length,
        total: list.length,
        totalPages: 1,
      },
    };
  },

  add: async (
    company: Omit<
      Company,
      "id" | "usage_count" | "last_used" | "created_at" | "updated_at"
    >,
  ): Promise<SingleResponse<Company>> => {
    await delay();
    const now = new Date().toISOString().slice(0, 10);
    const created: Company = {
      ...company,
      id: nextCompanyId++,
      usage_count: 0,
      last_used: now,
      created_at: now,
      updated_at: now,
    };
    companies.push(created);
    return { data: created, message: "거래처가 추가되었습니다" };
  },

  update: async (
    id: number,
    company: Partial<Company>,
  ): Promise<SingleResponse<Company>> => {
    await delay();
    const idx = companies.findIndex((c) => c.id === id);
    if (idx === -1) throw new Error("거래처를 찾을 수 없습니다");
    companies[idx] = {
      ...companies[idx],
      ...company,
      updated_at: new Date().toISOString().slice(0, 10),
    };
    return { data: companies[idx], message: "거래처가 수정되었습니다" };
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    await delay();
    companies = companies.filter((c) => c.id !== id);
    return { data: null, message: "거래처가 삭제되었습니다" };
  },
};

// --- Item Suggestions API ---

export const mockItemSuggestionsAPI = {
  getList: async (
    query?: string,
    category?: string,
  ): Promise<ListResponse<Item>> => {
    await delay();
    let list = [...items];
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((i) => i.item_name.toLowerCase().includes(q));
    }
    if (category) {
      list = list.filter((i) => i.category === category);
    }
    list.sort((a, b) => (b.usage_count ?? 0) - (a.usage_count ?? 0));
    return {
      data: list,
      pagination: {
        page: 1,
        limit: list.length,
        total: list.length,
        totalPages: 1,
      },
    };
  },

  add: async (
    item: Omit<
      Item,
      "id" | "usage_count" | "last_used" | "created_at" | "updated_at"
    >,
  ): Promise<SingleResponse<Item>> => {
    await delay();
    const now = new Date().toISOString().slice(0, 10);
    const created: Item = {
      ...item,
      id: nextItemId++,
      usage_count: 0,
      last_used: now,
      created_at: now,
      updated_at: now,
    };
    items.push(created);
    return { data: created, message: "품목이 추가되었습니다" };
  },

  update: async (
    id: number,
    item: Partial<Item>,
  ): Promise<SingleResponse<Item>> => {
    await delay();
    const idx = items.findIndex((i) => i.id === id);
    if (idx === -1) throw new Error("품목을 찾을 수 없습니다");
    items[idx] = {
      ...items[idx],
      ...item,
      updated_at: new Date().toISOString().slice(0, 10),
    };
    return { data: items[idx], message: "품목이 수정되었습니다" };
  },

  delete: async (id: number): Promise<SingleResponse<null>> => {
    await delay();
    items = items.filter((i) => i.id !== id);
    return { data: null, message: "품목이 삭제되었습니다" };
  },
};

// --- Settings API ---

export const mockSettingsAPI = {
  getIssuer: async (): Promise<SingleResponse<Issuer>> => {
    await delay();
    return { data: { ...issuer } };
  },

  saveIssuer: async (data: Issuer): Promise<SingleResponse<Issuer>> => {
    await delay();
    issuer = { ...data };
    return { data: issuer, message: "발행자 정보가 저장되었습니다" };
  },

  getAppSettings: async (): Promise<SingleResponse<AppSettings>> => {
    await delay();
    return { data: appSettings };
  },

  updateAppSettings: async (
    settings: Partial<AppSettings>,
  ): Promise<SingleResponse<AppSettings>> => {
    await delay();
    appSettings = { ...appSettings, ...settings };
    return { data: appSettings, message: "설정이 저장되었습니다" };
  },
};

export const mockSalespersonAPI = {
  getList: async () => {
    await delay();
    const sortedSalespeople = [...salespeople].sort((a, b) => {
      if (a.is_active !== b.is_active) return b.is_active - a.is_active;
      return a.sort_order - b.sort_order;
    });
    return {
      data: sortedSalespeople,
      pagination: {
        page: 1,
        limit: sortedSalespeople.length,
        total: sortedSalespeople.length,
        totalPages: 1,
      },
    };
  },

  create: async (input: { name: string; sort_order?: number }) => {
    await delay();
    const name = input.name.trim();
    if (!name) throw new Error("이름은 필수입니다.");
    if (salespeople.some((s) => s.is_active === 1 && s.name === name))
      throw new Error("이미 등록된 영업사원 이름입니다.");

    const sp: Salesperson = {
      id: nextSalespersonId++,
      name,
      sort_order: input.sort_order ?? salespeople.length,
      is_active: 1,
      created_at: new Date().toISOString(),
    };
    salespeople = [...salespeople, sp];
    return { data: sp };
  },

  update: async (id: number, input: Partial<Salesperson>) => {
    await delay();
    const idx = salespeople.findIndex((s) => s.id === id);
    if (idx < 0) throw new Error("영업사원을 찾을 수 없습니다.");
    if (input.name) {
      const n = input.name.trim();
      if (
        salespeople.some(
          (s) => s.id !== id && s.is_active === 1 && s.name === n,
        )
      )
        throw new Error("이미 등록된 영업사원 이름입니다.");
    }
    salespeople = salespeople.map((s) =>
      s.id === id ? { ...s, ...input, name: (input.name ?? s.name).trim() } : s,
    );
    return { data: salespeople[idx] };
  },

  remove: async (id: number) => {
    await delay();
    salespeople = salespeople.map((s) =>
      s.id === id ? { ...s, is_active: 0 as const } : s,
    );
    return { data: null };
  },
};

export const mockSalesRecordAPI = {
  getMonthly: async (year: number, month: number) => {
    await delay();
    const prefix = `${year}-${String(month).padStart(2, "0")}`;
    const records = salesRecords.filter((r) => r.work_date.startsWith(prefix));
    const recordSpIds = new Set(records.map((r) => r.salesperson_id));
    const visible = salespeople.filter(
      (s) => s.is_active === 1 || recordSpIds.has(s.id!),
    );
    const data: MonthlySalesData = {
      salespeople: visible,
      records,
    };
    return { data };
  },

  upsert: async (input: SalesRecordUpsertInput) => {
    await delay();
    const sp = salespeople.find((s) => s.id === input.salesperson_id);
    if (!sp) throw new Error("영업사원을 찾을 수 없습니다.");

    const existing = salesRecords.find(
      (r) =>
        r.salesperson_id === input.salesperson_id &&
        r.work_date === input.work_date,
    );
    if (existing) {
      salesRecords = salesRecords.map((r) =>
        r === existing
          ? { ...r, quantity: input.quantity, snapshot_name: sp.name }
          : r,
      );
      return {
        data: { ...existing, quantity: input.quantity, snapshot_name: sp.name },
      };
    }
    const record: SalesRecord = {
      id: nextSalesRecordId++,
      salesperson_id: input.salesperson_id,
      work_date: input.work_date,
      quantity: input.quantity,
      snapshot_name: sp.name,
    };
    salesRecords = [...salesRecords, record];
    return { data: record };
  },

  remove: async (id: number) => {
    await delay();
    salesRecords = salesRecords.filter((r) => r.id !== id);
    return { data: null };
  },
};

// --- Curation API (검수 큐레이션) ---

let curationJobs: CurationJobDetail[] = JSON.parse(
  JSON.stringify(mockCurationJobDetails),
);

// 세대 토큰의 mock 구현 — 서버는 ocr_jobs.updated_at을 초 단위로 캐스팅해 발급하고, 쓰기가
// 있을 때마다 값이 튄다. mock이 이걸 흉내 내지 않으면(발급도 대조도 안 하면) 훅이 토큰을
// 안 싣거나 직렬화가 깨진 회귀가 mock 모드에서 전부 통과해 실서버에서만 드러난다.
const bumpJobToken = (token: string): string => String(Number(token) + 1);

function assertJobToken(jobId: number, sent: string | undefined): void {
  const job = curationJobs.find((j) => j.job_id === jobId);
  if (!job) return;
  if (!sent) throw new Error("job_token이 필요합니다 (mock 400)");
  if (sent !== job.job_token) {
    throw new Error(
      `다른 곳에서 이 잡이 변경되었습니다 (mock 409) — 보낸 토큰 ${sent}, 현재 ${job.job_token}`,
    );
  }
}

// 서버는 status !== 'done' 잡의 쓰기를 409로 거부한다(curation_service의 patch_pair ·
// mark_reviewed). mock이 흉내 내지 않으면 재처리 큐에 든 잡의 편집이 mock 모드·e2e에서만
// 통과해 실서버에서만 드러난다 — 세대 토큰을 미러하는 것과 같은 이유다. 상태별 메시지
// 분기(#93)는 화면 문구와 백엔드의 몫이고, mock은 거부 사실만 미러한다.
function assertJobWritable(jobId: number): void {
  const job = curationJobs.find((j) => j.job_id === jobId);
  if (!job || job.status === "done") return;
  throw new Error(
    `아직 처리가 끝나지 않은 잡입니다 (mock 409) — 현재 상태 ${job.status}`,
  );
}

// 백엔드는 MySQL DATETIME(naive)을 jsonable_encoder로 "2026-06-30T08:30:00" 형태로 낸다.
// toISOString()은 UTC "Z" + 밀리초라 서버가 만들 수 없는 값이고, 시드(mocks/curation.ts)와도
// 형태가 갈린다 — 코드베이스의 iso.slice(5,16) 관용구가 KST 9시간 어긋난 값을 보이게 된다.
const toServerDateTime = (d: Date): string => {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${formatYYYYMMDD(d)}T${hh}:${mm}:${ss}`;
};

const toSummary = (job: CurationJobDetail): CurationJobSummary => ({
  job_id: job.job_id,
  invoice_id: job.invoice_id,
  curation_reviewed: job.curation_reviewed,
  curation_reviewed_at: job.curation_reviewed_at,
  pair_count: job.pairs.length,
  unreviewed_count: job.pairs.filter((p) => p.reviewed_at === null).length,
  created_at: job.created_at,
});

export const mockCurationAPI = {
  getJobs: async (params?: { page?: number; limit?: number }) => {
    await delay();
    // 서버 정렬 갈음: 미검수(false) 우선, 그다음 최신 생성순.
    const sorted = [...curationJobs].sort((a, b) => {
      if (a.curation_reviewed !== b.curation_reviewed) {
        return a.curation_reviewed ? 1 : -1;
      }
      return b.created_at.localeCompare(a.created_at);
    });
    const page = params?.page ?? 1;
    const limit = params?.limit ?? 20;
    const total = sorted.length;
    const start = (page - 1) * limit;
    return {
      data: sorted.slice(start, start + limit).map(toSummary),
      pagination: { page, limit, total, totalPages: Math.ceil(total / limit) },
    };
  },

  getJob: async (jobId: number) => {
    await delay();
    const found = curationJobs.find((j) => j.job_id === jobId);
    if (!found) throw new Error("잡을 찾을 수 없습니다");
    return { data: JSON.parse(JSON.stringify(found)) as CurationJobDetail };
  },

  patchPair: async (id: number, patch: CurationPairPatchBody) => {
    await delay();
    // 세대 대조·회전을 흉내 낸다. 없으면 훅이 토큰을 안 싣거나 직렬화가 깨진 회귀가
    // mock 모드·e2e에서 전부 GREEN으로 통과해, 실서버에서만 409로 드러난다.
    const owner = curationJobs.find((j) => j.pairs.some((p) => p.id === id));
    if (owner) {
      // 순서는 서버와 같다(status → token). 뒤집으면 재처리로 토큰이 튄 잡에서 mock만
      // 토큰 메시지를 내어, 사람이 새로고침하면 통과한다고 오인한다.
      assertJobWritable(owner.job_id);
      assertJobToken(owner.job_id, patch.job_token);
    }
    let result: CurationPairPatchResult | null = null;
    curationJobs = curationJobs.map((job) => {
      const touched = job.pairs.some((p) => p.id === id);
      const pairs = job.pairs.map((p) => {
        if (p.id !== id) return p;
        // job_token은 잡 단위 필드라 pair patch 본문에는 실리지만 pair 자체에는 속하지
        // 않는다 — 떼어내지 않으면 mock의 pair 저장소에 새어 들어간다.
        const { job_token: _jobToken, ...pairPatch } = patch;
        const updated = {
          ...p,
          ...pairPatch,
          // 서버 파생 쓰기 미러: status='excluded'면 같은 UPDATE에서 exclusion_reason도
          // NULL로 갱신된다(curation_repository.update_pair, ADR 0006). 포함 방향은 지우지 않는다.
          ...(patch.status === "excluded" ? { exclusion_reason: null } : {}),
          // 수정된 쌍은 재확인 대상으로 되돌아간다(Issue #52 spec §3.1) — 서버가 같은
          // UPDATE에서 reviewed_at = NULL을 쓴다. 채우는 방향이면 mock 모드·E2E가
          // 서버와 반대 상태를 유지한다.
          reviewed_at: null,
        };
        // PATCH 응답 형태: job_id + job_curation_reviewed + job_token 포함, top5·uncertain
        // 제외(계약 비대칭 — curation_service.py의 patch_pair 참조). mock은 세대를
        // 흉내 내지 않으므로 잡의 현재 토큰을 그대로 돌려준다.
        const { top5: _top5, uncertain: _uncertain, ...base } = updated;
        result = {
          ...base,
          job_id: job.job_id,
          // 해제는 무조건이라 서버도 상수 false를 돌려준다(spec §3.4).
          job_curation_reviewed: false,
          // 쓰기가 있었으니 세대가 튄다 — 서버의 ON UPDATE CURRENT_TIMESTAMP 미러.
          job_token: bumpJobToken(job.job_token),
        };
        return updated;
      });
      // 소속 잡의 게이트를 해제한다. curation_reviewed_at은 지우지 않는다(§3.2) —
      // 그래야 "재검수 필요"와 "미검수"가 갈린다. 건드리지 않은 잡은 참조를 그대로
      // 유지한다(불필요한 새 객체 생성 방지).
      return touched
        ? {
            ...job,
            curation_reviewed: false,
            job_token: bumpJobToken(job.job_token),
            pairs,
          }
        : job;
    });
    if (!result) throw new Error("쌍을 찾을 수 없습니다");
    return { data: result as CurationPairPatchResult };
  },

  reviewJob: async (jobId: number, jobToken: string) => {
    await delay();
    if (!curationJobs.some((j) => j.job_id === jobId)) {
      throw new Error("잡을 찾을 수 없습니다");
    }
    // 토큰을 무시하면 빈 문자열도 항상 성공해, 실서버에서 확정 409가 되는 경로가
    // mock에서만 조용히 통과한다(PATCH와 같은 이유).
    assertJobWritable(jobId);
    assertJobToken(jobId, jobToken);
    // 서버는 한 트랜잭션의 단일 CURRENT_TIMESTAMP로 잡·쌍 스탬프를 함께 찍는다
    // (mark_reviewed) — mock도 한 번만 만든 시각을 공유해 "잡과 쌍이 같은 시각"
    // 시드 불변식을 보장한다.
    const now = toServerDateTime(new Date());
    curationJobs = curationJobs.map((job) =>
      job.job_id === jobId
        ? {
            ...job,
            curation_reviewed: true,
            // 서버의 COALESCE 미러 — 첫 검수 시각만 채우고 재확정 때는 유지한다.
            curation_reviewed_at: job.curation_reviewed_at ?? now,
            pairs: job.pairs.map((p) => ({
              ...p,
              reviewed_at: p.reviewed_at ?? now,
            })),
          }
        : job,
    );
    return { data: { job_id: jobId, curation_reviewed: true } };
  },
};
