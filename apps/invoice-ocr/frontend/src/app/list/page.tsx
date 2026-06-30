import * as React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { FileTextIcon } from "lucide-react";
import { toast } from "sonner";

import type { Invoice, InvoiceFilters } from "@/types/invoice";
import { useInvoices } from "@/hooks/use-invoices";
import { useSettings } from "@/hooks/use-settings";
import { useDebounce } from "@/hooks/use-debounce";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useCompanies } from "@/hooks/use-companies";
import { invoiceAPI } from "@/services/api";
import type { Company } from "@/types/company";
import {
  buildSmsCustomerMessage,
  buildSmsInternalMessage,
  getCompanySmsTargetLabel,
} from "@/utils/formatters";
import { copyText } from "@/utils/clipboard";

import { PageContainer, PageHeader } from "@/components/layout";
import { SearchInput } from "@/components/ui/search-input";
import { FilterChips } from "@/components/ui/filter-chips";
import { SelectionBar } from "@/components/ui/selection-bar";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { getVisiblePages } from "@/lib/pagination";
import {
  InvoiceCard,
  InvoicePreview,
  MonthCalendar,
  type DaySummary,
} from "@/components/invoice";

const PERIOD_OPTIONS = [
  { label: "전체", value: "all" },
  { label: "이번 달", value: "this_month" },
  { label: "지난 달", value: "last_month" },
  { label: "3개월", value: "3_months" },
];

const SORT_OPTIONS = [
  { value: "date_desc", label: "최신순" },
  { value: "date_asc", label: "오래된순" },
  { value: "amount_desc", label: "금액 높은순" },
  { value: "amount_asc", label: "금액 낮은순" },
  { value: "company_asc", label: "거래처명순" },
];

function getDateRange(period: string): {
  date_from?: string;
  date_to?: string;
} {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  switch (period) {
    case "this_month":
      return { date_from: `${y}-${String(m + 1).padStart(2, "0")}-01` };
    case "last_month": {
      const pm = m === 0 ? 11 : m - 1;
      const py = m === 0 ? y - 1 : y;
      const lastDay = new Date(py, pm + 1, 0).getDate();
      return {
        date_from: `${py}-${String(pm + 1).padStart(2, "0")}-01`,
        date_to: `${py}-${String(pm + 1).padStart(2, "0")}-${lastDay}`,
      };
    }
    case "3_months": {
      const d = new Date(y, m - 2, 1);
      return {
        date_from: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`,
      };
    }
    default:
      return {};
  }
}

const SORT_MAP: Record<
  string,
  {
    sort_by: InvoiceFilters["sort_by"];
    sort_order: InvoiceFilters["sort_order"];
  }
> = {
  date_desc: { sort_by: "date", sort_order: "desc" },
  date_asc: { sort_by: "date", sort_order: "asc" },
  amount_desc: { sort_by: "amount", sort_order: "desc" },
  amount_asc: { sort_by: "amount", sort_order: "asc" },
  company_asc: { sort_by: "company", sort_order: "asc" },
};

function parseSortOption(val: string) {
  return SORT_MAP[val] ?? SORT_MAP["date_desc"];
}

function sortInvoices(invoices: Invoice[], sortKey: string): Invoice[] {
  const sorted = [...invoices];
  switch (sortKey) {
    case "date_desc":
      return sorted.sort((a, b) => b.issue_date.localeCompare(a.issue_date));
    case "date_asc":
      return sorted.sort((a, b) => a.issue_date.localeCompare(b.issue_date));
    case "amount_desc":
      return sorted.sort((a, b) => b.grand_total - a.grand_total);
    case "amount_asc":
      return sorted.sort((a, b) => a.grand_total - b.grand_total);
    case "company_asc":
      return sorted.sort((a, b) =>
        (a.recipient ?? "").localeCompare(b.recipient ?? ""),
      );
    default:
      return sorted;
  }
}

type SearchScope = "year" | "month" | "day";

export default function InvoiceListPage() {
  const navigate = useNavigate();
  const { issuer } = useSettings();
  const { data: companies } = useCompanies();

  const isDesktop = useMediaQuery("(min-width: 1024px)");

  // Calendar state (PC only)
  const today = new Date();
  const [calYear, setCalYear] = React.useState(today.getFullYear());
  const [calMonth, setCalMonth] = React.useState(today.getMonth());
  const [selectedDate, setSelectedDate] = React.useState<string | null>(
    `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`,
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const scopeParam = searchParams.get("scope");
  const searchScope: SearchScope =
    scopeParam === "month" || scopeParam === "day" ? scopeParam : "year";

  // PDF preview state
  const [previewInvoice, setPreviewInvoice] = React.useState<Invoice | null>(
    null,
  );

  const handlePdf = async (invoice: Invoice) => {
    if (!issuer) {
      toast.error(
        "발행자 정보를 불러오는 중입니다. 잠시 후 다시 시도해주세요.",
      );
      return;
    }
    if (!invoice.id) return;
    try {
      const res = await invoiceAPI.getById(invoice.id);
      setPreviewInvoice(res.data);
    } catch {
      toast.error("거래명세서 상세 정보를 불러올 수 없습니다.");
    }
  };

  const companyByName = React.useMemo(
    () => new Map(companies.map((c) => [c.company_name, c])),
    [companies],
  );
  const findCompanyByRecipient = (recipient: string): Company | null =>
    companyByName.get(recipient) ?? null;

  const handleSmsCustomer = async (invoice: Invoice) => {
    if (!issuer) {
      toast.error(
        "발행자 정보를 불러오는 중입니다. 잠시 후 다시 시도해주세요.",
      );
      return;
    }
    const company = findCompanyByRecipient(invoice.recipient);
    const message = buildSmsCustomerMessage(invoice, company, issuer);
    try {
      await copyText(message);
      toast.success("고객 SMS 메시지가 복사되었습니다");
    } catch {
      toast.error("클립보드 복사에 실패했습니다");
    }
  };

  const handleSmsInternal = async (invoice: Invoice) => {
    const company = findCompanyByRecipient(invoice.recipient);
    const message = buildSmsInternalMessage(
      invoice,
      company,
      company?.sms_number_type,
    );
    try {
      await copyText(message);
      toast.success("내부 SMS 메시지가 복사되었습니다");
    } catch {
      toast.error("클립보드 복사에 실패했습니다");
    }
  };

  // Filter state
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [period, setPeriod] = React.useState("all");
  const [sort, setSort] = React.useState("date_desc");
  const [page, setPage] = React.useState(1);
  const limit = 20;

  const dateRange = getDateRange(period);
  const sortOpt = parseSortOption(sort);

  const filters: InvoiceFilters = {
    search: debouncedSearch || undefined,
    ...dateRange,
    ...sortOpt,
    page,
    limit,
  };

  const { data: invoices, total, loading, refetch } = useInvoices(filters);
  const totalPages = Math.max(1, Math.ceil(total / limit));

  const visiblePages = React.useMemo(
    () => getVisiblePages(page, totalPages),
    [page, totalPages],
  );

  // Selection
  const [selected, setSelected] = React.useState<Set<number>>(new Set());
  const handleSelect = (id: number, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = React.useState<number | null>(null);

  const handleDelete = async (id: number) => {
    try {
      await invoiceAPI.delete(id);
      toast.success("삭제되었습니다");
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      refetch();
    } catch {
      toast.error("삭제에 실패했습니다");
    }
  };

  const handleBulkDelete = async () => {
    const results = await Promise.allSettled(
      Array.from(selected).map((id) => invoiceAPI.delete(id)),
    );
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.filter((r) => r.status === "rejected").length;
    if (failed > 0) toast.error(`${failed}개 삭제 실패`);
    if (succeeded > 0) toast.success(`${succeeded}개 삭제 완료`);
    setSelected(new Set());
    refetch();
  };

  const handleClone = (invoice: Invoice) => {
    navigate("/", {
      state: {
        cloneData: {
          ...invoice,
          id: undefined,
          created_at: undefined,
          updated_at: undefined,
          issue_date: new Date().toISOString().slice(0, 10),
        },
      },
    });
  };

  // Calendar month data (PC only)
  const calDateFrom = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-01`;
  const calLastDay = new Date(calYear, calMonth + 1, 0).getDate();
  const calDateTo = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${calLastDay}`;

  const { data: calInvoices } = useInvoices(
    isDesktop
      ? {
          date_from: calDateFrom,
          date_to: calDateTo,
          limit: 500,
          sort_by: "date",
          sort_order: "desc",
        }
      : { limit: 0 },
  );

  // 일별 그룹핑
  const daySummaries = React.useMemo(() => {
    const map: Record<string, DaySummary> = {};
    for (const inv of calInvoices) {
      const key = inv.issue_date;
      if (!map[key]) map[key] = { count: 0, totalAmount: 0 };
      map[key].count += 1;
      map[key].totalAmount += inv.grand_total;
    }
    return map;
  }, [calInvoices]);

  // 선택 날짜의 명세서 목록
  const selectedDayInvoices = React.useMemo(() => {
    if (!selectedDate) return [];
    return calInvoices.filter((inv) => {
      if (inv.issue_date !== selectedDate) return false;
      if (!debouncedSearch) return true;
      const q = debouncedSearch.toLowerCase();
      return (
        inv.recipient?.toLowerCase().includes(q) ||
        inv.vehicle_no?.toLowerCase().includes(q)
      );
    });
  }, [calInvoices, selectedDate, debouncedSearch]);

  // 월 범위 필터링 (클라이언트)
  const monthFilteredInvoices = React.useMemo(() => {
    if (!debouncedSearch) return calInvoices;
    const q = debouncedSearch.toLowerCase();
    return calInvoices.filter(
      (inv) =>
        inv.recipient?.toLowerCase().includes(q) ||
        inv.vehicle_no?.toLowerCase().includes(q),
    );
  }, [calInvoices, debouncedSearch]);

  // 연도 범위 API 호출
  const yearDateFrom = `${calYear}-01-01`;
  const yearDateTo = `${calYear}-12-31`;
  const [yearPage, setYearPage] = React.useState(1);
  const yearLimit = 20;

  const yearSortOpt = parseSortOption(sort);
  const {
    data: yearInvoices,
    total: yearTotal,
    loading: yearLoading,
  } = useInvoices(
    isDesktop && searchScope === "year"
      ? {
          search: debouncedSearch || undefined,
          date_from: yearDateFrom,
          date_to: yearDateTo,
          ...yearSortOpt,
          page: yearPage,
          limit: yearLimit,
        }
      : { limit: 0 },
  );
  const yearTotalPages = Math.max(1, Math.ceil(yearTotal / yearLimit));

  // scope에 따른 표시 데이터 (정렬 포함)
  const displayInvoices = React.useMemo(() => {
    switch (searchScope) {
      case "day":
        return sortInvoices(selectedDayInvoices, sort);
      case "month":
        return sortInvoices(monthFilteredInvoices, sort);
      case "year":
        return yearInvoices; // 서버에서 이미 정렬됨
    }
  }, [
    searchScope,
    selectedDayInvoices,
    monthFilteredInvoices,
    yearInvoices,
    sort,
  ]);

  // scope에 따른 건수
  const displayCount =
    searchScope === "year" ? yearTotal : displayInvoices.length;

  // scope 변경 핸들러
  const handleScopeChange = (scope: SearchScope) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (scope === "year") next.delete("scope");
        else next.set("scope", scope);
        return next;
      },
      { replace: true },
    );
    if (scope === "year") setYearPage(1);
  };

  // 연도 모드 페이지네이션
  const yearVisiblePages = React.useMemo(
    () =>
      searchScope === "year" ? getVisiblePages(yearPage, yearTotalPages) : [],
    [searchScope, yearPage, yearTotalPages],
  );

  // 드롭다운 옵션 텍스트
  const scopeOptions = React.useMemo(() => {
    const monthLabel = `${calMonth + 1}월`;
    const dayLabel = selectedDate
      ? (() => {
          const d = new Date(selectedDate + "T00:00:00");
          return `${d.getMonth() + 1}월 ${d.getDate()}일`;
        })()
      : monthLabel;
    return [
      { value: "year" as const, label: `${calYear}년` },
      { value: "month" as const, label: monthLabel },
      { value: "day" as const, label: dayLabel },
    ];
  }, [calYear, calMonth, selectedDate]);

  return (
    <>
      {/* PC 레이아웃: 달력 + 리스트 분할 */}
      {isDesktop ? (
        <>
          <PageHeader title="거래명세서 목록" showBack={false} />
          <PageContainer className="py-4">
            <div className="hidden lg:flex lg:items-center lg:justify-between lg:pb-4">
              <h1 className="text-xl font-semibold">거래명세서 목록</h1>
            </div>
            <div className="grid grid-cols-[1fr_1fr] gap-6 lg:h-[calc(100dvh-var(--page-shell-offset))]">
              {/* 좌측: 달력 */}
              <MonthCalendar
                year={calYear}
                month={calMonth}
                daySummaries={daySummaries}
                selectedDate={selectedDate}
                onDateSelect={(date) => {
                  setSelectedDate(date);
                  handleScopeChange("day");
                }}
                onMonthChange={(y, m) => {
                  setCalYear(y);
                  setCalMonth(m);
                }}
              />

              {/* 우측: 검색 범위별 리스트 */}
              <div className="flex min-h-0 flex-col">
                <div aria-live="polite">
                  <h2 className="mb-3 text-base font-semibold">
                    {searchScope === "year" && `${calYear}년`}
                    {searchScope === "month" &&
                      `${calYear}년 ${calMonth + 1}월`}
                    {searchScope === "day" &&
                      (selectedDate
                        ? (() => {
                            const d = new Date(selectedDate + "T00:00:00");
                            return new Intl.DateTimeFormat("ko-KR", {
                              year: "numeric",
                              month: "long",
                              day: "numeric",
                              weekday: "short",
                            }).format(d);
                          })()
                        : "날짜를 선택하세요")}
                    {` · ${displayCount}건`}
                  </h2>
                </div>

                <div className="flex items-center gap-2 pb-3">
                  <Select
                    value={searchScope}
                    onValueChange={(v) => handleScopeChange(v as SearchScope)}
                  >
                    <SelectTrigger
                      aria-label="검색 범위"
                      className="h-12 w-auto shrink-0 gap-1 rounded-xl text-sm"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {scopeOptions.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <SearchInput
                    value={search}
                    onChange={(v) => {
                      setSearch(v);
                      setPage(1);
                      if (searchScope === "year") setYearPage(1);
                    }}
                    placeholder="거래처명 또는 차량번호 검색"
                  />
                  <Select value={sort} onValueChange={setSort}>
                    <SelectTrigger
                      aria-label="정렬"
                      className="h-12 w-auto shrink-0 gap-1 rounded-xl text-sm"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SORT_OPTIONS.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto">
                  {searchScope === "year" && yearLoading ? (
                    <div className="space-y-3">
                      {Array.from({ length: 3 }).map((_, i) => (
                        <Skeleton key={i} className="h-28 w-full rounded-xl" />
                      ))}
                    </div>
                  ) : displayInvoices.length === 0 ? (
                    <EmptyState
                      icon={FileTextIcon}
                      title={
                        searchScope === "day"
                          ? "해당 날짜에 발행된 명세서가 없습니다"
                          : debouncedSearch
                            ? "검색 결과가 없습니다"
                            : "해당 기간에 발행된 명세서가 없습니다"
                      }
                      description={
                        searchScope === "day"
                          ? "다른 날짜를 선택하거나 새 명세서를 작성해보세요"
                          : "검색어를 변경하거나 다른 기간을 선택해보세요"
                      }
                    />
                  ) : (
                    <div className="space-y-3">
                      {displayInvoices.map((inv) => {
                        const company = findCompanyByRecipient(inv.recipient);
                        return (
                          <InvoiceCard
                            key={inv.id}
                            invoice={inv}
                            selected={inv.id ? selected.has(inv.id) : false}
                            smsTargetLabel={getCompanySmsTargetLabel(
                              company,
                              company?.sms_number_type,
                            )}
                            onSelect={handleSelect}
                            onClone={handleClone}
                            onDelete={(id) => setDeleteTarget(id)}
                            onPdf={handlePdf}
                            onSmsCustomer={handleSmsCustomer}
                            onSmsInternal={handleSmsInternal}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>

                {displayInvoices.length > 0 && (
                  <div className="border-border mt-3 flex shrink-0 items-center justify-between rounded-lg border px-3 py-2">
                    <span className="text-muted-foreground text-sm">
                      {searchScope === "year"
                        ? "연간 합계"
                        : searchScope === "month"
                          ? "월간 합계"
                          : "일별 합계"}
                    </span>
                    <span className="text-sm font-semibold">
                      {displayInvoices
                        .reduce((sum, inv) => sum + inv.grand_total, 0)
                        .toLocaleString("ko-KR")}
                      원
                    </span>
                  </div>
                )}

                {searchScope === "year" && yearTotalPages > 1 && (
                  <Pagination className="mt-3 shrink-0">
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious
                          onClick={() => setYearPage((p) => Math.max(1, p - 1))}
                          aria-disabled={yearPage <= 1}
                          tabIndex={yearPage <= 1 ? -1 : undefined}
                          className={
                            yearPage <= 1
                              ? "pointer-events-none opacity-50"
                              : ""
                          }
                        />
                      </PaginationItem>
                      {yearVisiblePages[0] > 1 && (
                        <PaginationItem>
                          <PaginationEllipsis />
                        </PaginationItem>
                      )}
                      {yearVisiblePages.map((p) => (
                        <PaginationItem key={p}>
                          <PaginationLink
                            isActive={p === yearPage}
                            onClick={() => setYearPage(p)}
                          >
                            {p}
                          </PaginationLink>
                        </PaginationItem>
                      ))}
                      {yearVisiblePages[yearVisiblePages.length - 1] <
                        yearTotalPages && (
                        <PaginationItem>
                          <PaginationEllipsis />
                        </PaginationItem>
                      )}
                      <PaginationItem>
                        <PaginationNext
                          onClick={() =>
                            setYearPage((p) => Math.min(yearTotalPages, p + 1))
                          }
                          aria-disabled={yearPage >= yearTotalPages}
                          tabIndex={yearPage >= yearTotalPages ? -1 : undefined}
                          className={
                            yearPage >= yearTotalPages
                              ? "pointer-events-none opacity-50"
                              : ""
                          }
                        />
                      </PaginationItem>
                    </PaginationContent>
                  </Pagination>
                )}
              </div>
            </div>
          </PageContainer>
        </>
      ) : (
        <>
          <PageHeader title="거래명세서 목록" showBack={false} />
          <PageContainer className="space-y-4 py-4">
            <SearchInput
              value={search}
              onChange={(v) => {
                setSearch(v);
                setPage(1);
              }}
              placeholder="거래처명 또는 차량번호 검색"
            />

            <FilterChips
              options={PERIOD_OPTIONS}
              value={period}
              onChange={(v) => {
                setPeriod(v);
                setPage(1);
              }}
            />

            <div
              className="flex items-center justify-between"
              aria-live="polite"
            >
              <span className="text-muted-foreground text-xs">{total}건</span>
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger
                  size="sm"
                  aria-label="정렬"
                  className="w-auto gap-1 text-xs"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-28 w-full rounded-xl" />
                ))}
              </div>
            ) : invoices.length === 0 ? (
              <EmptyState
                icon={FileTextIcon}
                title="거래명세서가 없습니다"
                description="새 거래명세서를 작성해보세요"
                action={{
                  label: "새 거래명세서",
                  onClick: () => navigate("/"),
                }}
              />
            ) : (
              <div className="space-y-3">
                {invoices.map((inv) => (
                  <InvoiceCard
                    key={inv.id}
                    invoice={inv}
                    selected={inv.id ? selected.has(inv.id) : false}
                    onSelect={handleSelect}
                    onClone={handleClone}
                    onDelete={(id) => setDeleteTarget(id)}
                    onPdf={handlePdf}
                    onSmsCustomer={handleSmsCustomer}
                    onSmsInternal={handleSmsInternal}
                  />
                ))}
              </div>
            )}

            {totalPages > 1 && (
              <Pagination>
                <PaginationContent>
                  <PaginationItem>
                    <PaginationPrevious
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      aria-disabled={page <= 1}
                      tabIndex={page <= 1 ? -1 : undefined}
                      className={
                        page <= 1 ? "pointer-events-none opacity-50" : ""
                      }
                    />
                  </PaginationItem>
                  {visiblePages[0] > 1 && (
                    <PaginationItem>
                      <PaginationEllipsis />
                    </PaginationItem>
                  )}
                  {visiblePages.map((p) => (
                    <PaginationItem key={p}>
                      <PaginationLink
                        isActive={p === page}
                        onClick={() => setPage(p)}
                      >
                        {p}
                      </PaginationLink>
                    </PaginationItem>
                  ))}
                  {visiblePages[visiblePages.length - 1] < totalPages && (
                    <PaginationItem>
                      <PaginationEllipsis />
                    </PaginationItem>
                  )}
                  <PaginationItem>
                    <PaginationNext
                      onClick={() =>
                        setPage((p) => Math.min(totalPages, p + 1))
                      }
                      aria-disabled={page >= totalPages}
                      tabIndex={page >= totalPages ? -1 : undefined}
                      className={
                        page >= totalPages
                          ? "pointer-events-none opacity-50"
                          : ""
                      }
                    />
                  </PaginationItem>
                </PaginationContent>
              </Pagination>
            )}
          </PageContainer>
        </>
      )}

      {/* 공유 오버레이 - 한 번만 렌더링 */}
      <SelectionBar count={selected.size} onDelete={handleBulkDelete} />

      {previewInvoice && issuer && (
        <InvoicePreview
          invoice={previewInvoice}
          issuer={issuer}
          onClose={() => setPreviewInvoice(null)}
        />
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={() => setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>거래명세서 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              이 거래명세서를 삭제하시겠습니까? 삭제 후 복구할 수 없습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteTarget) handleDelete(deleteTarget);
                setDeleteTarget(null);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
