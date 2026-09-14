import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDownIcon, ChevronRightIcon, InboxIcon } from "lucide-react";

import {
  HERMES_PAGE_SIZE,
  useHermesEntries,
  useHermesSummary,
} from "@/hooks/use-hermes-entries";
import { useHermesKnowledgeVersions } from "@/hooks/use-hermes-knowledge-versions";
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
  HERMES_HIGHLIGHT_NEW_CLASS,
  HERMES_HIGHLIGHT_OLD_CLASS,
  HERMES_MISMATCH_LABELS,
  HERMES_STATUSES,
  HERMES_STATUS_CLASSES,
  HERMES_STATUS_LABELS,
  formatAmount,
  formatRate,
  hermesEntryUrl,
  knowledgeVersionNote,
  summarizeKnowledgeChanges,
} from "@/utils/hermes";
import type {
  HermesKnowledgeChange,
  HermesKnowledgeVersion,
  HermesTotals,
  HermesVersionRow,
} from "@/types/hermes";

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
    versions,
    loading: versionsLoading,
    error: versionsError,
  } = useHermesKnowledgeVersions();
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
          {versionsError && (
            <p className="text-muted-foreground mt-2 text-sm">
              지식 버전 이력을 불러오지 못했습니다: {versionsError}
            </p>
          )}
          {!versionsLoading &&
            (versions.length > 0 || summary.by_version.length > 0) && (
              <VersionTable
                versions={versions}
                byVersion={summary.by_version}
              />
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
                    highlight={e.mismatch_fields.includes("recipient")}
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
                    highlight={e.mismatch_fields.includes("item_count")}
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
                    highlight={e.mismatch_fields.includes("grand_total")}
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

// 초안값 → 최종값. highlight는 서버 mismatch_fields가 판정한다(값 재비교 금지 — spec §6.
// 발행일 열은 대조 축이 아니라 이 컴포넌트를 쓰지 않는다).
function DraftFinal({
  draft,
  final,
  highlight,
}: {
  draft: string;
  final: string | null;
  highlight: boolean;
}) {
  if (final === null)
    return <span className="text-muted-foreground">{draft} → —</span>;
  if (!highlight) return <span>{final}</span>;
  return (
    <span>
      <span className={HERMES_HIGHLIGHT_OLD_CLASS}>{draft}</span>{" "}
      <span className={HERMES_HIGHLIGHT_NEW_CLASS}>{final}</span>
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

interface VersionTableRow {
  key: string;
  /** 0은 발행 이전 구간. */
  version: number;
  publishedAt: string | null;
  knowledge: HermesKnowledgeVersion | null;
  stats: HermesVersionRow | null;
}

// 지식 버전 이력(최신순)과 버전별 일치율을 버전 번호로 합친다. 이력 API가 죽어도
// by_version만으로 행을 만들고, 발행 이전(0) 구간은 항상 맨 아래다. 거부 기록은 같은
// 번호가 두 번 올 수 있어 key에 발행 시각을 섞는다.
function mergeVersionRows(
  versions: HermesKnowledgeVersion[],
  byVersion: HermesVersionRow[],
): VersionTableRow[] {
  const stats = new Map(byVersion.map((r) => [r.version, r]));
  const listed = new Set(versions.map((v) => v.version));
  const fromKnowledge = versions.map((v) => ({
    key: `${v.version}-${v.published_at}`,
    version: v.version,
    publishedAt: v.published_at,
    knowledge: v,
    stats: v.rejected === null ? (stats.get(v.version) ?? null) : null,
  }));
  // toSorted는 tsconfig lib(ES2020)에 없다 — filter가 이미 새 배열이라 sort가 원본을 건드리지 않는다.
  const statsOnly = byVersion
    .filter((r) => r.version !== 0 && !listed.has(r.version))
    .sort((a, b) => b.version - a.version)
    .map((r) => ({
      key: `${r.version}-stats`,
      version: r.version,
      publishedAt: null,
      knowledge: null,
      stats: r,
    }));
  const before = stats.get(0);
  const beforeRow = before
    ? [
        {
          key: "0",
          version: 0,
          publishedAt: null,
          knowledge: null,
          stats: before,
        },
      ]
    : [];
  return [...fromKnowledge, ...statsOnly, ...beforeRow];
}

function VersionTable({
  versions,
  byVersion,
}: {
  versions: HermesKnowledgeVersion[];
  byVersion: HermesVersionRow[];
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const rows = mergeVersionRows(versions, byVersion);
  return (
    <table data-testid="version-table" className="mt-4 w-full text-sm">
      <thead className="text-muted-foreground border-b text-left">
        <tr>
          <th className="py-2">지식 버전</th>
          <th>발행</th>
          <th>건수</th>
          <th>품목명</th>
          <th>공급가</th>
          <th>무수정률</th>
          <th>변경</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const changes = r.knowledge?.changes ?? [];
          const isOpen = expanded === r.key;
          return (
            <VersionRow
              key={r.key}
              row={r}
              changes={changes}
              isOpen={isOpen}
              onToggle={() => setExpanded(isOpen ? null : r.key)}
            />
          );
        })}
      </tbody>
    </table>
  );
}

function VersionRow({
  row,
  changes,
  isOpen,
  onToggle,
}: {
  row: VersionTableRow;
  changes: HermesKnowledgeChange[];
  isOpen: boolean;
  onToggle: () => void;
}) {
  const label = row.version === 0 ? "발행 이전" : `v${row.version}`;
  const note = row.knowledge ? knowledgeVersionNote(row.knowledge) : null;
  const canExpand = changes.length > 0;
  const Chevron = isOpen ? ChevronDownIcon : ChevronRightIcon;
  return (
    <>
      <tr className="border-b">
        <td className="py-2">
          {canExpand ? (
            <button
              type="button"
              aria-expanded={isOpen}
              aria-label={`${label} 변경 펼치기`}
              className="focus-visible:ring-ring inline-flex items-center gap-1 rounded hover:underline focus-visible:ring-2 focus-visible:outline-none"
              onClick={onToggle}
            >
              <Chevron className="size-3.5" aria-hidden="true" />
              {label}
            </button>
          ) : (
            label
          )}
        </td>
        <td className="text-muted-foreground tabular-nums">
          {shortDate(row.publishedAt)}
        </td>
        <td className="tabular-nums">{row.stats ? row.stats.count : "—"}</td>
        <td className="tabular-nums">
          {row.stats ? formatRate(row.stats.name_rate, row.stats.pairs) : "—"}
        </td>
        <td className="tabular-nums">
          {row.stats ? formatRate(row.stats.supply_rate, row.stats.pairs) : "—"}
        </td>
        <td className="tabular-nums">
          {row.stats
            ? formatRate(row.stats.untouched_rate, row.stats.count)
            : "—"}
        </td>
        <td>
          {note !== null ? (
            <span className="text-muted-foreground text-xs">{note}</span>
          ) : (
            <div
              data-testid="knowledge-change-chips"
              className="flex flex-wrap gap-1"
            >
              {summarizeKnowledgeChanges(changes).map((chip) => (
                <span
                  key={chip}
                  className="bg-muted rounded px-1.5 py-0.5 text-xs"
                >
                  {chip}
                </span>
              ))}
            </div>
          )}
        </td>
      </tr>
      {isOpen && (
        <tr className="border-b">
          <td colSpan={7} className="py-2 pl-6">
            <ChangeDetail changes={changes} />
          </td>
        </tr>
      )}
    </>
  );
}

// 항목 텍스트 앞에 그룹 라벨(등급명·소제목)을 붙인다 — 어휘 절은 같은 텍스트가
// 등급마다 의미가 다르다.
function itemText(group: string, text: string): string {
  return group ? `[${group}] ${text}` : text;
}

function ChangeDetail({ changes }: { changes: HermesKnowledgeChange[] }) {
  return (
    <div data-testid="knowledge-change-detail" className="space-y-3">
      {changes.map((c) => (
        <div key={c.section}>
          <p className="mb-1 text-xs font-semibold">{c.section}</p>
          <ul className="space-y-0.5 text-xs">
            {c.added.map((i) => (
              <li key={`+${i.group}${i.text}`} className="text-green-600">
                + {itemText(i.group, i.text)}
              </li>
            ))}
            {c.removed.map((i) => (
              <li
                key={`-${i.group}${i.text}`}
                className={HERMES_HIGHLIGHT_OLD_CLASS}
              >
                − {itemText(i.group, i.text)}
              </li>
            ))}
            {c.moved.map((m) => (
              <li key={`↔${m.text}`}>
                ↔ {m.text}{" "}
                <span className="text-muted-foreground">
                  {m.from} → {m.to}
                </span>
              </li>
            ))}
            {c.changed.map((v) => (
              <li key={`~${v.group}${v.key}`}>
                ~ {itemText(v.group, v.key)}{" "}
                <span className={HERMES_HIGHLIGHT_OLD_CLASS}>{v.before}</span> →{" "}
                <span className={HERMES_HIGHLIGHT_NEW_CLASS}>{v.after}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
