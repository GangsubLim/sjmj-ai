import { Fragment } from "react";
import { cn } from "@/lib/utils";
import { formatPrice } from "@/utils/formatters";
import { isHoliday, isToday } from "@/utils/calendar";
import type { MonthGridCell } from "@/utils/calendar";
import type { Salesperson } from "@/types/salesperson";
import type { SalesRecord } from "@/types/sales-record";
import { sumPerSalesperson, sumTotal } from "./aggregation";

/** 영업사원 열(7rem) + 간격(24px) + 7일(각 90px) + 간격(24px) + 주 합계 열(8rem) */
export const GRID_TEMPLATE =
  "grid-cols-[7rem_24px_repeat(7,90px)_24px_8rem]";

interface Props {
  weekIndex: number;
  cells: MonthGridCell[];
  salespeople: Salesperson[];
  recordsByDate: Map<string, Map<number, SalesRecord>>;
  onDayClick: (date: string) => void;
}

const cellBase = "border-border border px-2 py-1.5 text-base";
const headerStrip = "bg-muted/40 text-center font-semibold";

export function SalesPerformanceWeek({
  weekIndex,
  cells,
  salespeople,
  recordsByDate,
  onDayClick,
}: Props) {
  const roster = sumPerSalesperson(cells, recordsByDate, salespeople);
  const weekTotal = sumTotal(roster);

  const dailyTotalOf = (cell: MonthGridCell): number => {
    if (!cell.inMonth) return 0;
    const recs = recordsByDate.get(cell.date);
    if (!recs) return 0;
    let sum = 0;
    for (const r of recs.values()) sum += r.quantity;
    return sum;
  };

  return (
    <div className={`grid ${GRID_TEMPLATE}`}>
      {/* 헤더: N주차 + 날짜 + 주 합계 */}
      <div className={cn(cellBase, headerStrip)}>{weekIndex + 1}주차</div>
      <div aria-hidden="true" />
      {cells.map((cell, dIdx) =>
        cell.inMonth ? (
          <button
            key={cell.date}
            type="button"
            onClick={() => onDayClick(cell.date)}
            aria-label={`${cell.date} 실적 입력`}
            className={cn(
              cellBase,
              "hover:bg-accent/50 cursor-pointer text-center font-semibold",
              (dIdx === 0 || isHoliday(cell.date)) && "text-red-500",
              isToday(cell.date) && "bg-primary/10",
            )}
          >
            {parseInt(cell.date.slice(8), 10)}
          </button>
        ) : (
          <div key={cell.date} aria-hidden="true" />
        ),
      )}
      <div aria-hidden="true" />
      <div className={cn(cellBase, headerStrip)}>주 합계</div>

      {/* 영업사원별 실적 행 */}
      {roster.length === 0 ? (
        <>
          <div className={cn(cellBase, "text-muted-foreground text-center")}>
            —
          </div>
          <div aria-hidden="true" />
          {cells.map((cell) =>
            cell.inMonth ? (
              <div key={cell.date} className={cellBase} />
            ) : (
              <div key={cell.date} aria-hidden="true" />
            ),
          )}
          <div aria-hidden="true" />
          <div className={cellBase} />
        </>
      ) : (
        roster.map((sp) => (
          <Fragment key={sp.id}>
            <div
              className={cn(
                cellBase,
                "truncate text-center",
                sp.isActive === 0 && "text-muted-foreground",
              )}
            >
              {sp.name}
            </div>
            <div aria-hidden="true" />
            {cells.map((cell) => {
              if (!cell.inMonth)
                return <div key={cell.date} aria-hidden="true" />;
              const rec = recordsByDate.get(cell.date)?.get(sp.id);
              return (
                <div
                  key={cell.date}
                  className={cn(
                    cellBase,
                    "text-center tabular-nums",
                    sp.isActive === 0 && "text-muted-foreground",
                  )}
                >
                  {rec ? formatPrice(rec.quantity) : ""}
                </div>
              );
            })}
            <div aria-hidden="true" />
            <div
              className={cn(
                cellBase,
                "text-center font-medium tabular-nums",
                sp.isActive === 0 && "text-muted-foreground",
              )}
            >
              {formatPrice(sp.quantity)}
            </div>
          </Fragment>
        ))
      )}

      {/* 합계 행 */}
      {weekTotal > 0 && (
        <>
          <div className={cn(cellBase, "bg-muted/20 text-center font-bold")}>
            합계
          </div>
          <div aria-hidden="true" />
          {cells.map((cell) => {
            if (!cell.inMonth) return <div key={cell.date} aria-hidden="true" />;
            const dt = dailyTotalOf(cell);
            return (
              <div
                key={cell.date}
                className={cn(cellBase, "text-center font-bold tabular-nums")}
              >
                {dt > 0 ? formatPrice(dt) : ""}
              </div>
            );
          })}
          <div aria-hidden="true" />
          <div
            className={cn(
              cellBase,
              "bg-muted/20 text-center font-bold tabular-nums",
            )}
          >
            {formatPrice(weekTotal)}
          </div>
        </>
      )}
    </div>
  );
}
