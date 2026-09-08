import { useNavigate } from "react-router-dom";
import { InboxIcon } from "lucide-react";

import {
  HERMES_PAGE_SIZE,
  useHermesEntries,
  useHermesSummary,
} from "@/hooks/use-hermes-entries";
import { PageContainer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
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
  HERMES_MISMATCH_LABELS,
  HERMES_STATUSES,
  HERMES_STATUS_CLASSES,
  HERMES_STATUS_LABELS,
  formatAmount,
  formatRate,
  hermesEntryUrl,
} from "@/utils/hermes";
import type { HermesTotals, HermesVersionRow } from "@/types/hermes";

function shortDate(iso: string | null): string {
  return iso ? iso.slice(5, 10) : "—";
}

export default function HermesStatusPage() {
  const navigate = useNavigate();
  const {
    summary,
    loading: summaryLoading,
    error: summaryError,
  } = useHermesSummary();
  const {
    data,
    total,
    page,
    totalPages,
    loading,
    error,
    setPage,
    status,
    setStatus,
  } = useHermesEntries(HERMES_PAGE_SIZE);
  const visiblePages = getVisiblePages(page, totalPages);

  const goToEntry = (id: number) => navigate(hermesEntryUrl(id, page, status));

  return (
    <PageContainer className="py-4">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold">hermes 위임 입력 현황</h1>
        <span className="text-muted-foreground text-sm">총 {total}건</span>
      </div>

      {summaryLoading && <Skeleton className="h-24 w-full" />}
      {summaryError && (
        <p className="text-muted-foreground mb-3 text-sm">
          요약을 불러오지 못했습니다: {summaryError}
        </p>
      )}
      {summary && (
        <>
          <SummaryCards totals={summary.totals} />
          <div
            data-testid="knowledge-line"
            className="text-muted-foreground mt-2 text-sm"
          >
            판독 지식{" "}
            <span className="text-foreground font-medium">
              v{summary.knowledge.version}
            </span>
            {" · 발행 "}
            {summary.knowledge.published_at?.slice(0, 10) ?? "—"}
            {" · 누적 교정 "}
            {summary.knowledge.corrections}건
          </div>
          {summary.by_version.length > 0 && (
            <VersionTable rows={summary.by_version} />
          )}
        </>
      )}

      <div className="mt-4 mb-2 flex items-center gap-2">
        {HERMES_STATUSES.map((s) => (
          <Button
            key={s}
            type="button"
            variant={status === s ? "default" : "outline"}
            size="sm"
            aria-pressed={status === s}
            // 켜진 필터를 다시 누르면 꺼진다 — 전체로 돌아갈 별도 버튼을 두지 않는다.
            onClick={() => setStatus(status === s ? null : s)}
          >
            {HERMES_STATUS_LABELS[s]}
          </Button>
        ))}
      </div>

      {error && (
        <p className="text-destructive py-8 text-center text-sm">{error}</p>
      )}

      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {!loading && !error && data.length === 0 && (
        <EmptyState
          icon={InboxIcon}
          title={
            status ? "이 상태의 건이 없습니다" : "hermes로 들어온 건이 없습니다"
          }
          description={
            status
              ? "필터를 끄면 전체 목록이 보입니다."
              : "텔레그램으로 전표 사진을 보내면 여기에 표시됩니다."
          }
        />
      )}

      {!loading && !error && data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground border-b text-left">
            <tr>
              <th className="py-2">건</th>
              <th>발행일</th>
              <th>수신처</th>
              <th>품목</th>
              <th>합계</th>
              <th>상태</th>
              <th>불일치</th>
            </tr>
          </thead>
          <tbody>
            {/* 행 전체는 마우스 편의용 onClick, 키보드·SR 진입점은 셀 내부 네이티브 button
                (테이블 행/셀 시맨틱 보존 — app/curation/page.tsx와 같은 관례). */}
            {data.map((e) => (
              <tr
                key={e.id}
                className="hover:bg-muted/50 cursor-pointer border-b"
                onClick={() => goToEntry(e.id)}
              >
                <td className="py-2 font-medium">
                  <button
                    type="button"
                    aria-label={`#${e.id} 상세`}
                    className="focus-visible:ring-ring rounded font-medium hover:underline focus-visible:ring-2 focus-visible:outline-none"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      goToEntry(e.id);
                    }}
                  >
                    #{e.id}
                  </button>
                </td>
                {/* 발행일은 정보로만 표시한다 — hermes가 항상 오늘로 채우고 사람이
                    /edit에서 실제 거래일을 넣는 것이 설계된 절차라, 다름을 강조하면
                    사실상 전건이 빨개진다(spec §3.2). */}
                <td className="text-muted-foreground tabular-nums">
                  {shortDate(e.issue_date_draft)} →{" "}
                  {shortDate(e.issue_date_final)}
                </td>
                <td>
                  <DraftFinal
                    draft={e.recipient_draft ?? "—"}
                    final={e.recipient_final}
                  />
                </td>
                <td className="tabular-nums">
                  <DraftFinal
                    draft={String(e.item_count_draft)}
                    final={
                      e.item_count_final === null
                        ? null
                        : String(e.item_count_final)
                    }
                  />
                </td>
                <td className="tabular-nums">
                  <DraftFinal
                    draft={formatAmount(e.grand_total_draft)}
                    final={
                      e.grand_total_final === null
                        ? null
                        : formatAmount(e.grand_total_final)
                    }
                  />
                </td>
                <td>
                  <span
                    data-testid="entry-status"
                    className={HERMES_STATUS_CLASSES[e.status]}
                  >
                    {HERMES_STATUS_LABELS[e.status]}
                  </span>
                </td>
                <td>
                  <div
                    data-testid="mismatch-chips"
                    className="flex flex-wrap gap-1"
                  >
                    {e.mismatch_fields.map((f) => (
                      <span
                        key={f}
                        className="bg-muted rounded px-1.5 py-0.5 text-xs"
                      >
                        {HERMES_MISMATCH_LABELS[f]}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!loading && !error && totalPages > 1 && (
        <Pagination className="mt-4">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                onClick={() => setPage(Math.max(1, page - 1))}
                aria-disabled={page <= 1}
                tabIndex={page <= 1 ? -1 : undefined}
                className={page <= 1 ? "pointer-events-none opacity-50" : ""}
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
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                aria-disabled={page >= totalPages}
                tabIndex={page >= totalPages ? -1 : undefined}
                className={
                  page >= totalPages ? "pointer-events-none opacity-50" : ""
                }
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      )}
    </PageContainer>
  );
}

// 초안값 → 최종값. 다르면 최종값을 강조한다(발행일 열은 이 컴포넌트를 쓰지 않는다).
function DraftFinal({ draft, final }: { draft: string; final: string | null }) {
  if (final === null)
    return <span className="text-muted-foreground">{draft} → —</span>;
  if (final === draft) return <span>{draft}</span>;
  return (
    <span>
      <span className="text-muted-foreground line-through">{draft}</span>{" "}
      <span className="font-medium text-amber-600">{final}</span>
    </span>
  );
}

function SummaryCards({ totals }: { totals: HermesTotals }) {
  const cards: { label: string; value: string }[] = [
    { label: "건수", value: `${totals.count} (삭제 ${totals.missing})` },
    {
      label: "무수정률",
      value: formatRate(totals.untouched_rate, totals.count),
    },
    { label: "품목명", value: formatRate(totals.name_rate, totals.pairs) },
    { label: "공급가", value: formatRate(totals.supply_rate, totals.pairs) },
    {
      label: "합계",
      value: formatRate(totals.grand_total_rate, totals.count),
    },
  ];
  return (
    <div
      data-testid="summary-cards"
      className="grid grid-cols-2 gap-2 sm:grid-cols-5"
    >
      {cards.map((c) => (
        <div key={c.label} className="rounded border p-3">
          <p className="text-muted-foreground text-xs">{c.label}</p>
          <p className="text-lg font-semibold tabular-nums">{c.value}</p>
        </div>
      ))}
    </div>
  );
}

function VersionTable({ rows }: { rows: HermesVersionRow[] }) {
  return (
    <table className="mt-4 w-full text-sm">
      <thead className="text-muted-foreground border-b text-left">
        <tr>
          <th className="py-2">지식 버전</th>
          <th>건수</th>
          <th>품목명</th>
          <th>공급가</th>
          <th>무수정률</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.version} className="border-b">
            {/* version 0은 지식 발행 이전 구간이다. */}
            <td className="py-2">
              {r.version === 0 ? "발행 이전" : `v${r.version}`}
            </td>
            <td className="tabular-nums">{r.count}</td>
            <td className="tabular-nums">{formatRate(r.name_rate, r.pairs)}</td>
            <td className="tabular-nums">
              {formatRate(r.supply_rate, r.pairs)}
            </td>
            <td className="tabular-nums">
              {formatRate(r.untouched_rate, r.count)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
