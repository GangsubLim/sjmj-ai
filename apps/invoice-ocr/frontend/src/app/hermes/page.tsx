import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  InboxIcon,
  TriangleAlertIcon,
} from "lucide-react";

import {
  HERMES_PAGE_SIZE,
  useHermesEntries,
  useHermesSummary,
} from "@/hooks/use-hermes-entries";
import { useHermesKnowledgeVersions } from "@/hooks/use-hermes-knowledge-versions";
import { PageContainer, SectionHeader } from "@/components/layout";
import { HermesStatusBadge } from "@/components/hermes/HermesStatusBadge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
  HERMES_STATUS_ICONS,
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

// bg-muted 단독은 페이지 배경과 1.05:1이라 칩 경계가 사실상 보이지 않았다 — 테두리로 가른다.
const CHIP_CLASS =
  "border-border rounded border bg-muted px-1.5 py-0.5 text-xs";

export default function HermesStatusPage() {
  const navigate = useNavigate();
  const {
    summary,
    loading: summaryLoading,
    error: summaryError,
    refetch: refetchSummary,
  } = useHermesSummary();
  const { versions, error: versionsError } = useHermesKnowledgeVersions();
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
    refetch,
  } = useHermesEntries(HERMES_PAGE_SIZE);
  const visiblePages = getVisiblePages(page, totalPages);
  // 목록만 필터를 타고 요약·버전 표는 전체 기준으로 남는다 — 두 모집단이 한 화면에서
  // 같은 무게로 놓이면 분자와 분모를 섞어 읽게 되므로, 총계에 스코프를 적어 가른다.
  const totalLabel = status
    ? `${HERMES_STATUS_LABELS[status]} ${total}건` +
      (summary ? ` · 전체 ${summary.totals.count}건` : "")
    : `총 ${total}건`;

  const goToEntry = (id: number) => navigate(hermesEntryUrl(id, page, status));

  return (
    <PageContainer className="py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">hermes 위임 입력 현황</h1>
        <span
          data-testid="list-total"
          className="text-muted-foreground text-sm"
        >
          {/* 로딩 중 total은 아직 0이다 — 그대로 그리면 "건이 없다"를 확정값으로 말한다. */}
          {loading ? "집계 중" : totalLabel}
        </span>
      </div>

      {/* settings의 진입 링크가 lg 이상에서만 뜨는 운영자 진단 화면이다(기존 관례). 작은
          화면에서도 URL로는 닿으므로, 렌더를 막는 대신 어느 화면에 맞춘 것인지 밝힌다. */}
      <p className="bg-muted text-muted-foreground mb-3 rounded px-3 py-2 text-xs lg:hidden">
        데스크톱에 맞춘 진단 화면 — 표는 가로로 밀어서 봅니다
      </p>

      {summaryLoading && <Skeleton className="h-24 w-full" />}
      {summaryError && (
        <div
          role="alert"
          className="border-destructive/40 text-destructive mb-3 flex items-center justify-between gap-3 rounded border px-3 py-2 text-sm"
        >
          <span>요약을 불러오지 못했습니다: {summaryError}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={refetchSummary}
          >
            다시 시도
          </Button>
        </div>
      )}
      {summary && (
        <>
          <p className="text-muted-foreground mb-1 text-xs">
            전체 기준 집계 — 상태 필터와 무관
          </p>
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
          {/* 이력 로딩을 기다리지 않는다 — 이 API에는 타임아웃이 없어 멈추면 기존
              by_version 표까지 무기한 가려진다. 이력은 도착하는 대로 합쳐진다. */}
          {(versions.length > 0 || summary.by_version.length > 0) && (
            <>
              <SectionHeader
                title="판독 지식 버전별 효험"
                rightContent={
                  <span className="text-muted-foreground text-xs">
                    전체 기준
                  </span>
                }
              />
              <VersionTable
                versions={versions}
                byVersion={summary.by_version}
              />
            </>
          )}
        </>
      )}

      <SectionHeader
        title="건별 대조"
        rightContent={
          <div className="flex items-center gap-2">
            {HERMES_STATUSES.map((s) => {
              const Icon = HERMES_STATUS_ICONS[s];
              return (
                <Button
                  key={s}
                  type="button"
                  variant={status === s ? "default" : "outline"}
                  size="sm"
                  aria-pressed={status === s}
                  // 켜진 필터를 다시 누르면 꺼진다 — 빈 결과에서는 아래 EmptyState가
                  // 끄는 버튼을 함께 준다.
                  onClick={() => setStatus(status === s ? null : s)}
                >
                  <Icon className="size-3.5" aria-hidden="true" />
                  {HERMES_STATUS_LABELS[s]}
                </Button>
              );
            })}
          </div>
        }
      />

      {/* 필터·페이지 전환은 화면을 바꾸지만 포커스는 버튼에 남는다 — 결과 도착을 알린다. */}
      <div aria-live="polite" aria-busy={loading}>
        {error && (
          <EmptyState
            icon={TriangleAlertIcon}
            title="목록을 불러오지 못했습니다"
            description={error}
            action={{ label: "다시 시도", onClick: refetch }}
          />
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
              status
                ? "이 상태의 건이 없습니다"
                : "hermes로 들어온 건이 없습니다"
            }
            description={
              status
                ? "필터를 끄면 전체 목록이 보입니다."
                : "텔레그램으로 전표 사진을 보내면 여기에 표시됩니다."
            }
            action={
              status
                ? { label: "필터 끄기", onClick: () => setStatus(null) }
                : undefined
            }
          />
        )}

        {!loading && !error && data.length > 0 && (
          <Table>
            <TableCaption className="sr-only">
              hermes 초안과 사람이 확정한 최종본의 건별 대조
            </TableCaption>
            <TableHeader className="text-muted-foreground">
              <TableRow>
                <TableHead>건</TableHead>
                <TableHead>거래일</TableHead>
                <TableHead>수신처</TableHead>
                <TableHead className="text-right">품목</TableHead>
                <TableHead className="text-right">합계</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>불일치</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* 행 전체는 마우스 편의용 onClick, 키보드·SR 진입점은 셀 내부 네이티브 button
                  (테이블 행/셀 시맨틱 보존 — app/curation/page.tsx와 같은 관례). */}
              {data.map((e) => (
                <TableRow
                  key={e.id}
                  className="cursor-pointer"
                  onClick={() => goToEntry(e.id)}
                >
                  <TableCell className="font-medium">
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
                  </TableCell>
                  {/* 초안 발행일은 hermes가 항상 오늘로 채워 전 행이 같은 값이라 대조 축이
                      아니다(spec §3.2) — 사람이 /edit에서 넣은 실제 거래일만 그리고,
                      연도까지 필요하면 title로 본다(shortDate가 월-일만 남긴다). */}
                  <TableCell
                    className="text-muted-foreground tabular-nums"
                    title={e.issue_date_final ?? undefined}
                  >
                    {shortDate(e.issue_date_final)}
                  </TableCell>
                  <TableCell>
                    <DraftFinal
                      draft={e.recipient_draft ?? "—"}
                      final={e.recipient_final}
                      highlight={e.mismatch_fields.includes("recipient")}
                    />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <DraftFinal
                      draft={String(e.item_count_draft)}
                      final={
                        e.item_count_final === null
                          ? null
                          : String(e.item_count_final)
                      }
                      highlight={e.mismatch_fields.includes("item_count")}
                    />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <DraftFinal
                      draft={formatAmount(e.grand_total_draft)}
                      final={
                        e.grand_total_final === null
                          ? null
                          : formatAmount(e.grand_total_final)
                      }
                      highlight={e.mismatch_fields.includes("grand_total")}
                    />
                  </TableCell>
                  <TableCell>
                    <HermesStatusBadge status={e.status} />
                  </TableCell>
                  <TableCell>
                    {/* flex-wrap이면 칩 칸이 열 최소폭에 기여하지 못해, 좁은 폭에서 칩이
                        세로로 쌓이며 불일치 행만 높이가 부푼다 — 한 줄로 두고 표를 민다. */}
                    <div
                      data-testid="mismatch-chips"
                      className="flex flex-nowrap gap-1"
                    >
                      {e.mismatch_fields.map((f) => (
                        <span key={f} className={CHIP_CLASS}>
                          {HERMES_MISMATCH_LABELS[f]}
                        </span>
                      ))}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

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
  // del/ins로 "무엇이 초안이고 무엇이 최종인지"를 시맨틱에 싣는다 — 취소선이 CSS뿐이면
  // 스크린리더에는 두 숫자가 구분 없이 이어져 낭독된다.
  return (
    <span>
      <del className={HERMES_HIGHLIGHT_OLD_CLASS}>
        <span className="sr-only">초안 </span>
        {draft}
      </del>{" "}
      <ins className={HERMES_HIGHLIGHT_NEW_CLASS}>
        <span className="sr-only">최종 </span>
        {final}
      </ins>
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
    <Table data-testid="version-table">
      <TableCaption className="sr-only">
        판독 지식 버전별 발행 시각과 그 버전이 만든 초안의 일치율
      </TableCaption>
      <TableHeader className="text-muted-foreground">
        <TableRow>
          <TableHead>지식 버전</TableHead>
          <TableHead>발행</TableHead>
          <TableHead className="text-right">건수</TableHead>
          <TableHead className="text-right">품목명</TableHead>
          <TableHead className="text-right">공급가</TableHead>
          <TableHead className="text-right">무수정률</TableHead>
          <TableHead>변경</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
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
      </TableBody>
    </Table>
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
  // 거부 기록의 version은 거부 당시 활성 버전이라 행이 말하는 버전은 시도한 v{N+1}이다.
  // 발행 이전(0) 구간 집계와 첫 발행 거부(version 0)가 같은 라벨로 겹치지 않게 한다.
  const label =
    row.knowledge?.rejected != null
      ? `v${row.version + 1}`
      : row.version === 0
        ? "발행 이전"
        : `v${row.version}`;
  const note = row.knowledge ? knowledgeVersionNote(row.knowledge) : null;
  const canExpand = changes.length > 0;
  const Chevron = isOpen ? ChevronDownIcon : ChevronRightIcon;
  return (
    <>
      <TableRow>
        <TableCell>
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
        </TableCell>
        <TableCell
          className="text-muted-foreground tabular-nums"
          title={row.publishedAt ?? undefined}
        >
          {shortDate(row.publishedAt)}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {row.stats ? row.stats.count : "—"}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {row.stats ? formatRate(row.stats.name_rate, row.stats.pairs) : "—"}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {row.stats ? formatRate(row.stats.supply_rate, row.stats.pairs) : "—"}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {row.stats
            ? formatRate(row.stats.untouched_rate, row.stats.count)
            : "—"}
        </TableCell>
        <TableCell>
          {note !== null ? (
            <span className="text-muted-foreground text-xs">{note}</span>
          ) : (
            <div
              data-testid="knowledge-change-chips"
              className="flex flex-nowrap gap-1"
            >
              {summarizeKnowledgeChanges(changes).map((chip) => (
                <span key={chip} className={CHIP_CLASS}>
                  {chip}
                </span>
              ))}
            </div>
          )}
        </TableCell>
      </TableRow>
      {isOpen && (
        <TableRow>
          {/* 어휘 diff는 문장이라 줄바꿈이 필요하다 — TableCell 기본 whitespace-nowrap을 푼다. */}
          <TableCell colSpan={7} className="pl-6 align-top whitespace-normal">
            <ChangeDetail changes={changes} />
          </TableCell>
        </TableRow>
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
              <li key={`+${i.group}${i.text}`} className="text-success">
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
