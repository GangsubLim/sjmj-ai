import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useSalesRecords } from "@/hooks/use-sales-records";
import { useCaptureShare } from "@/hooks/use-capture-share";
import { chunkIntoWeeks, getMonthGrid } from "@/utils/calendar";
import { SalesPerformanceHeader } from "./sales-performance-header";
import { SalesPerformanceWeek, GRID_TEMPLATE } from "./sales-performance-week";
import { SalesRecordInputDialog } from "./sales-record-input-dialog";
import { SalesPerformanceCapture } from "./sales-performance-capture";
import { MonthlyTotalsPanel } from "./monthly-totals-panel";
import { sumPerSalesperson, sumTotal } from "./aggregation";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

export function SalesPerformanceCalendar() {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [openDate, setOpenDate] = useState<string | null>(null);

  const { salespeople, recordsByDate, upsertRecord, removeRecord, refetch } =
    useSalesRecords(year, month);

  const captureRef = useRef<HTMLDivElement>(null);
  const captureShare = useCaptureShare({
    getNode: () => captureRef.current,
    filename: `sales-performance-${year}-${String(month).padStart(2, "0")}.png`,
    pixelRatio: 2,
    sizeCapBytes: 8 * 1024 * 1024,
    backgroundColor: "#ffffff",
  });

  const grid = useMemo(() => getMonthGrid(year, month), [year, month]);
  const weeks = useMemo(() => chunkIntoWeeks(grid), [grid]);

  const monthlyTotals = useMemo(
    () => sumPerSalesperson(grid, recordsByDate, salespeople),
    [grid, recordsByDate, salespeople],
  );
  const totalAmount = useMemo(() => sumTotal(monthlyTotals), [monthlyTotals]);

  const handleSaveDialog = async (
    upserts: { salesperson_id: number; quantity: number }[],
    deletes: number[],
  ) => {
    if (!openDate) return;
    try {
      for (const u of upserts) {
        await upsertRecord({
          salesperson_id: u.salesperson_id,
          work_date: openDate,
          quantity: u.quantity,
        });
      }
      for (const id of deletes) {
        await removeRecord(id);
      }
      await refetch();
      toast.success("저장되었습니다.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장 실패");
    }
  };

  const handleDownload = async () => {
    toast.message("PNG 생성 중…");
    try {
      await captureShare.capture();
      toast.success("PNG 다운로드 완료");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "캡쳐 실패");
    }
  };

  return (
    <div className="mx-auto max-w-[1280px] px-6 pb-12">
      <SalesPerformanceHeader
        year={year}
        month={month}
        isCapturing={captureShare.isCapturing}
        onYearChange={setYear}
        onMonthChange={setMonth}
        onDownload={handleDownload}
      />
      <div className={`mb-3 grid ${GRID_TEMPLATE}`}>
        <div className="col-span-9 flex items-center justify-center">
          <h1 className="text-2xl font-semibold">
            {month}월 영업사원 실적(수량)내역
          </h1>
        </div>
        <div aria-hidden="true" />
        <MonthlyTotalsPanel totals={monthlyTotals} monthTotal={totalAmount} />
      </div>

      <div className={`grid ${GRID_TEMPLATE}`}>
        <div aria-hidden="true" />
        <div aria-hidden="true" />
        {WEEKDAYS.map((d, i) => (
          <div
            key={d}
            className={cn(
              "bg-muted/40 border-border border px-2 py-1.5 text-center text-base font-semibold",
              i === 0 && "text-red-500",
            )}
          >
            {d}
          </div>
        ))}
        <div aria-hidden="true" />
        <div aria-hidden="true" />
      </div>

      <div className="mt-3 flex flex-col gap-3">
        {weeks.map((week, wIdx) => (
          <SalesPerformanceWeek
            key={wIdx}
            weekIndex={wIdx}
            cells={week}
            salespeople={salespeople}
            recordsByDate={recordsByDate}
            onDayClick={(d) => setOpenDate(d)}
          />
        ))}
      </div>

      {openDate && (
        <SalesRecordInputDialog
          open={true}
          date={openDate}
          salespeople={salespeople}
          recordsForDate={recordsByDate.get(openDate)}
          onClose={() => setOpenDate(null)}
          onSave={handleSaveDialog}
        />
      )}

      <div
        style={{
          position: "fixed",
          left: "-99999px",
          top: 0,
          pointerEvents: "none",
        }}
        aria-hidden="true"
      >
        <SalesPerformanceCapture
          ref={captureRef}
          year={year}
          month={month}
          salespeople={salespeople}
          recordsByDate={recordsByDate}
        />
      </div>
    </div>
  );
}
