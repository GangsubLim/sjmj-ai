import { forwardRef } from "react";
import { formatPrice } from "@/utils/formatters";
import { chunkIntoWeeks, getMonthGrid, isHoliday } from "@/utils/calendar";
import type { MonthGridCell } from "@/utils/calendar";
import { sumPerSalesperson, sumTotal } from "./aggregation";
import type { Salesperson } from "@/types/salesperson";
import type { SalesRecord } from "@/types/sales-record";

interface Props {
  year: number;
  month: number;
  salespeople: Salesperson[];
  recordsByDate: Map<string, Map<number, SalesRecord>>;
}

const BORDER_COLOR = "#a3a3a3";
const BORDER = `1px solid ${BORDER_COLOR}`;
const INNER_DIVIDER = `1px solid ${BORDER_COLOR}`;
const SUNDAY_COLOR = "#dc2626";
const HEADER_BG = "#f5f5f5";
const OUT_BG = "#fafafa";
const MUTED = "#999";
const FONT_FAMILY =
  "Inter, 'Noto Sans KR', ui-sans-serif, system-ui, sans-serif";

const NAME_W = 110;
const DAY_W = 90;
const GAP_W = 24;
const SUM_W = 123;

const baseCell: React.CSSProperties = {
  border: BORDER,
  padding: "6px 8px",
  fontSize: 15,
  verticalAlign: "middle",
};
const headerCell: React.CSSProperties = {
  ...baseCell,
  background: HEADER_BG,
  fontWeight: 600,
  fontSize: 16,
  textAlign: "center",
};
const gapCell: React.CSSProperties = {
  border: "none",
  padding: 0,
  width: GAP_W,
};

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

export const SalesPerformanceCapture = forwardRef<HTMLDivElement, Props>(
  ({ year, month, salespeople, recordsByDate }, ref) => {
    const grid = getMonthGrid(year, month);
    const weeks = chunkIntoWeeks(grid);
    const monthlyTotals = sumPerSalesperson(grid, recordsByDate, salespeople);
    const monthTotal = sumTotal(monthlyTotals);

    const dailyTotalOf = (cell: MonthGridCell): number => {
      if (!cell.inMonth) return 0;
      const recs = recordsByDate.get(cell.date);
      if (!recs) return 0;
      let sum = 0;
      for (const r of recs.values()) sum += r.quantity;
      return sum;
    };

    return (
      <div
        ref={ref}
        style={{
          width: "fit-content",
          padding: "64px",
          backgroundColor: "#fff",
          fontFamily: FONT_FAMILY,
          boxSizing: "border-box",
        }}
      >
        <header
          style={{
            marginBottom: 16,
            display: "grid",
            gridTemplateColumns: `${NAME_W + GAP_W + DAY_W * 7}px ${GAP_W}px ${SUM_W}px`,
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <h2 style={{ fontSize: 26, margin: 0 }}>
              영업사원 실적(수량)내역 {year}년 {month}월
            </h2>
          </div>
          <div />
          <div
            style={{
              border: BORDER,
              background: "#fff",
              fontSize: 15,
              boxSizing: "border-box",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div
              style={{
                padding: "6px 8px",
                textAlign: "center",
                background: HEADER_BG,
                borderBottom: INNER_DIVIDER,
                fontSize: 16,
                fontWeight: 600,
              }}
            >
              월 합계
            </div>
            <div style={{ padding: 8, flex: 1 }}>
              {monthlyTotals.length === 0 ? (
                <div style={{ color: MUTED }}>이번 달 실적 없음</div>
              ) : (
                monthlyTotals.map((t) => (
                  <div
                    key={t.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 4,
                      color: t.isActive === 0 ? MUTED : undefined,
                    }}
                  >
                    <span>{t.name}</span>
                    <span>{formatPrice(t.quantity)}</span>
                  </div>
                ))
              )}
              {monthTotal > 0 && (
                <div
                  style={{
                    marginTop: 4,
                    paddingTop: 4,
                    borderTop: INNER_DIVIDER,
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 4,
                    fontWeight: 700,
                  }}
                >
                  <span>합계</span>
                  <span>{formatPrice(monthTotal)}</span>
                </div>
              )}
            </div>
          </div>
        </header>

        <table
          style={{
            width: NAME_W + GAP_W + DAY_W * 7 + GAP_W + SUM_W,
            borderCollapse: "collapse",
            tableLayout: "fixed",
          }}
        >
          <colgroup>
            <col style={{ width: NAME_W }} />
            <col style={{ width: GAP_W }} />
            {Array.from({ length: 7 }, (_, i) => (
              <col key={i} style={{ width: DAY_W }} />
            ))}
            <col style={{ width: GAP_W }} />
            <col style={{ width: SUM_W }} />
          </colgroup>
          <thead>
            <tr>
              <td style={{ border: "none" }} />
              <td style={gapCell} />
              {WEEKDAYS.map((d, i) => (
                <th
                  key={d}
                  style={{
                    ...headerCell,
                    color: i === 0 ? SUNDAY_COLOR : undefined,
                  }}
                >
                  {d}
                </th>
              ))}
              <td style={gapCell} />
              <td style={{ border: "none" }} />
            </tr>
          </thead>
          <tbody>
            <tr>
              <td
                colSpan={11}
                style={{ height: GAP_W / 2, border: "none", padding: 0 }}
              />
            </tr>
          </tbody>
          {weeks.map((week, wIdx) => {
            const roster = sumPerSalesperson(week, recordsByDate, salespeople);
            const weekTotal = sumTotal(roster);
            return (
              <tbody key={wIdx}>
                {/* 주차 헤더 + 날짜 */}
                <tr>
                  <td style={{ ...headerCell }}>{wIdx + 1}주차</td>
                  <td style={gapCell} />
                  {week.map((cell, dIdx) =>
                    cell.inMonth ? (
                      <td
                        key={cell.date}
                        style={{
                          ...baseCell,
                          textAlign: "center",
                          fontWeight: 600,
                          background: HEADER_BG,
                          color:
                            dIdx === 0 || isHoliday(cell.date)
                              ? SUNDAY_COLOR
                              : undefined,
                        }}
                      >
                        {parseInt(cell.date.slice(8), 10)}
                      </td>
                    ) : (
                      <td key={cell.date} style={{ border: "none" }} />
                    ),
                  )}
                  <td style={gapCell} />
                  <td style={{ ...headerCell }}>주 합계</td>
                </tr>

                {/* 영업사원별 실적 */}
                {roster.length === 0 ? (
                  <tr>
                    <td
                      style={{ ...baseCell, textAlign: "center", color: MUTED }}
                    >
                      —
                    </td>
                    <td style={gapCell} />
                    {week.map((cell) =>
                      cell.inMonth ? (
                        <td key={cell.date} style={baseCell} />
                      ) : (
                        <td key={cell.date} style={{ border: "none" }} />
                      ),
                    )}
                    <td style={gapCell} />
                    <td style={baseCell} />
                  </tr>
                ) : (
                  roster.map((sp) => (
                    <tr key={sp.id}>
                      <td
                        style={{
                          ...baseCell,
                          textAlign: "center",
                          color: sp.isActive === 0 ? MUTED : undefined,
                        }}
                      >
                        {sp.name}
                      </td>
                      <td style={gapCell} />
                      {week.map((cell) => {
                        if (!cell.inMonth)
                          return (
                            <td key={cell.date} style={{ border: "none" }} />
                          );
                        const rec = recordsByDate.get(cell.date)?.get(sp.id);
                        return (
                          <td
                            key={cell.date}
                            style={{
                              ...baseCell,
                              textAlign: "center",
                              fontVariantNumeric: "tabular-nums",
                              color: sp.isActive === 0 ? MUTED : undefined,
                            }}
                          >
                            {rec ? formatPrice(rec.quantity) : ""}
                          </td>
                        );
                      })}
                      <td style={gapCell} />
                      <td
                        style={{
                          ...baseCell,
                          textAlign: "center",
                          fontWeight: 500,
                          fontVariantNumeric: "tabular-nums",
                          color: sp.isActive === 0 ? MUTED : undefined,
                        }}
                      >
                        {formatPrice(sp.quantity)}
                      </td>
                    </tr>
                  ))
                )}

                {/* 합계 */}
                {weekTotal > 0 && (
                  <tr>
                    <td
                      style={{
                        ...baseCell,
                        textAlign: "center",
                        background: OUT_BG,
                        fontWeight: 700,
                      }}
                    >
                      합계
                    </td>
                    <td style={gapCell} />
                    {week.map((cell) => {
                      if (!cell.inMonth)
                        return (
                          <td key={cell.date} style={{ border: "none" }} />
                        );
                      const dt = dailyTotalOf(cell);
                      return (
                        <td
                          key={cell.date}
                          style={{
                            ...baseCell,
                            textAlign: "center",
                            fontWeight: 700,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {dt > 0 ? formatPrice(dt) : ""}
                        </td>
                      );
                    })}
                    <td style={gapCell} />
                    <td
                      style={{
                        ...baseCell,
                        textAlign: "center",
                        fontWeight: 700,
                        fontVariantNumeric: "tabular-nums",
                        background: OUT_BG,
                      }}
                    >
                      {formatPrice(weekTotal)}
                    </td>
                  </tr>
                )}
                {wIdx < weeks.length - 1 && (
                  <tr>
                    <td
                      colSpan={11}
                      style={{ height: GAP_W / 2, border: "none", padding: 0 }}
                    />
                  </tr>
                )}
              </tbody>
            );
          })}
        </table>
      </div>
    );
  },
);
SalesPerformanceCapture.displayName = "SalesPerformanceCapture";
