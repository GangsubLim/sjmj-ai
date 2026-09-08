import { Link, useParams, useSearchParams } from "react-router-dom";

import { useHermesEntry } from "@/hooks/use-hermes-entry";
import { hermesPhotoUrl } from "@/services/api";
import { PageContainer } from "@/components/layout";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageParam } from "@/hooks/use-page-param";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";
import {
  HERMES_HIGHLIGHT_NEW_CLASS,
  HERMES_HIGHLIGHT_OLD_CLASS,
  HERMES_STATUS_CLASSES,
  HERMES_STATUS_LABELS,
  HERMES_STATUS_PARAM,
  formatAmount,
  hermesListUrl,
  parseHermesStatus,
} from "@/utils/hermes";
import type {
  HermesEntryDetail,
  HermesRow,
  HermesRowCell,
} from "@/types/hermes";

const PLACEHOLDER = placeholderSvg(240, 320);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

export default function HermesEntryPage() {
  const { id } = useParams<{ id: string }>();
  // 목록 복귀 좌표는 URL이 소유한다 — 목록에서 실어 보낸 page·필터를 그대로 되돌려준다.
  const { page } = usePageParam();
  const [searchParams] = useSearchParams();
  const status = parseHermesStatus(searchParams.get(HERMES_STATUS_PARAM));
  // 정수가 아닌 id는 훅에 넘기지 않는다(undefined면 훅이 요청하지 않는다).
  const entryId = id && /^\d+$/.test(id) ? Number(id) : undefined;
  const { entry, loading, error } = useHermesEntry(entryId);

  if (loading) {
    return (
      <PageContainer className="space-y-3 py-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </PageContainer>
    );
  }

  if (error || !entry) {
    return (
      <PageContainer className="py-4">
        <p className="text-destructive py-8 text-center text-sm">
          {error ?? "건을 찾을 수 없습니다"}
        </p>
        <p className="text-center text-sm">
          <Link to={hermesListUrl(page, status)} className="underline">
            목록으로
          </Link>
        </p>
      </PageContainer>
    );
  }

  // 전사값이 한 행도 없으면 열 자체를 숨긴다 — raw.json은 v0.18.0부터 쌓이는 산출물이라
  // 과거 건에서는 항상 비어 있고, 빈 열이 표 폭만 잡아먹는다(spec §6.2).
  const hasRaw = entry.rows.some((r) => r.raw !== null);

  return (
    <PageContainer className="py-4">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold">#{entry.id} 대조</h1>
        <span className={HERMES_STATUS_CLASSES[entry.status]}>
          {HERMES_STATUS_LABELS[entry.status]}
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div>
          {entry.has_photo ? (
            <img
              src={hermesPhotoUrl(entry.id)}
              alt="원본 사진"
              className="w-full rounded border"
              onError={handleImageError}
            />
          ) : (
            <p className="text-muted-foreground rounded border p-6 text-center text-sm">
              보관된 사진이 없습니다
            </p>
          )}
        </div>

        <div className="space-y-4">
          <HeaderCompare entry={entry} />
          <RowTable rows={entry.rows} hasRaw={hasRaw} />
        </div>
      </div>

      <div className="mt-6 flex items-center gap-4 text-sm">
        <Link to={`/edit/${entry.id}`} className="underline">
          이 건 교정하기
        </Link>
        <Link
          to={hermesListUrl(page, status)}
          className="text-muted-foreground underline"
        >
          목록으로
        </Link>
      </div>
    </PageContainer>
  );
}

function HeaderCompare({ entry }: { entry: HermesEntryDetail }) {
  const { draft, final } = entry;
  return (
    <div className="space-y-1 rounded border p-3 text-sm">
      {/* 발행일은 강조하지 않는다 — hermes 1단계가 발행일을 판독하지 않고 항상 오늘로
          채우며 실제 거래일은 사람이 /edit에서 넣는 것이 설계된 절차라, 다름을 불일치로
          그리면 사실상 전건이 빨개진다(spec §3.2). */}
      <p data-testid="issue-date-compare" className="text-muted-foreground">
        발행일 {draft.issue_date ?? "—"} → {final?.issue_date ?? "—"}
      </p>
      <CompareLine
        label="수신처"
        draft={draft.recipient ?? "—"}
        final={final?.recipient ?? null}
        highlight={entry.mismatch_fields.includes("recipient")}
      />
      <CompareLine
        label="합계"
        draft={formatAmount(draft.grand_total ?? null)}
        final={final ? formatAmount(final.grand_total) : null}
        highlight={entry.mismatch_fields.includes("grand_total")}
      />
    </div>
  );
}

// highlight는 서버 mismatch_fields가 판정한다(값 재비교 금지 — spec §6).
function CompareLine({
  label,
  draft,
  final,
  highlight,
}: {
  label: string;
  draft: string;
  final: string | null;
  highlight: boolean;
}) {
  return (
    <p>
      <span className="text-muted-foreground mr-2">{label}</span>
      <span className={highlight ? HERMES_HIGHLIGHT_OLD_CLASS : ""}>
        {highlight ? draft : (final ?? draft)}
      </span>
      {highlight && final !== null && (
        <span className={`ml-2 ${HERMES_HIGHLIGHT_NEW_CLASS}`}>{final}</span>
      )}
      {final === null && (
        <span className="text-muted-foreground ml-2">→ —</span>
      )}
    </p>
  );
}

function RowTable({ rows, hasRaw }: { rows: HermesRow[]; hasRaw: boolean }) {
  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground border-b text-left">
        <tr>
          <th className="w-8 py-2">#</th>
          {hasRaw && <th>전사값</th>}
          <th>초안</th>
          <th>최종본</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.index} className="border-b align-top">
            <td className="text-muted-foreground py-2 tabular-nums">
              {row.index + 1}
            </td>
            {hasRaw && (
              <td className="py-2">
                {row.raw ? (
                  <span>
                    {row.raw.text ?? "—"}
                    {row.raw.conf && (
                      <span className="bg-muted ml-1 rounded px-1 text-xs">
                        {row.raw.conf}
                      </span>
                    )}
                  </span>
                ) : (
                  "—"
                )}
              </td>
            )}
            <td className="py-2">
              <ItemCell cell={row.draft} highlight={row.mismatch} />
            </td>
            <td className="py-2">
              <ItemCell cell={row.final} highlight={row.mismatch} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ItemCell({
  cell,
  highlight,
}: {
  cell: HermesRowCell | null;
  highlight: ("name" | "supply")[];
}) {
  if (cell === null) return <span className="text-muted-foreground">—</span>;
  const nameClass = highlight.includes("name")
    ? HERMES_HIGHLIGHT_NEW_CLASS
    : "";
  const supplyClass = highlight.includes("supply")
    ? HERMES_HIGHLIGHT_NEW_CLASS
    : "";
  return (
    <div>
      <span className={nameClass}>{cell.name ?? "—"}</span>
      <div className="text-muted-foreground text-xs tabular-nums">
        {cell.quantity ?? "—"}
        {cell.unit ?? ""} × {formatAmount(cell.unit_price)} ={" "}
        <span className={supplyClass}>{formatAmount(cell.supply)}</span>
        {cell.deduction && <span className="ml-1">(차감)</span>}
      </div>
    </div>
  );
}
