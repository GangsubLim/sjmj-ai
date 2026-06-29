import { cn } from "@/lib/utils";
import { formatPrice } from "@/utils/formatters";
import type { SalespersonTotal } from "./aggregation";

interface Props {
  totals: SalespersonTotal[];
  monthTotal: number;
}

export function MonthlyTotalsPanel({ totals, monthTotal }: Props) {
  return (
    <div className="border-border flex flex-col border bg-white">
      <div className="bg-muted/40 border-border border-b px-2 py-1.5 text-center text-base font-semibold">
        월 합계
      </div>
      <div className="flex flex-1 flex-col gap-1 p-2 text-base">
        <div className="flex-1 space-y-1">
          {totals.length === 0 ? (
            <p className="text-muted-foreground">이번 달 실적 없음</p>
          ) : (
            totals.map((t) => (
              <div
                key={t.id}
                className={cn(
                  "flex justify-between gap-1",
                  t.isActive === 0 && "text-muted-foreground",
                )}
              >
                <span className="truncate">{t.name}</span>
                <span className="tabular-nums">{formatPrice(t.quantity)}</span>
              </div>
            ))
          )}
        </div>
        {monthTotal > 0 && (
          <div className="border-border flex justify-between gap-1 border-t pt-1.5 font-bold">
            <span>합계</span>
            <span className="tabular-nums">{formatPrice(monthTotal)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
