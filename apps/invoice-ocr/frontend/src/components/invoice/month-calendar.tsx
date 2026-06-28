import * as React from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { abbreviateAmount, formatPrice } from "@/utils/formatters";

export interface DaySummary {
  count: number;
  totalAmount: number;
}

interface MonthCalendarProps {
  year: number;
  month: number; // 0-11
  daySummaries: Record<string, DaySummary>; // "YYYY-MM-DD" → summary
  selectedDate: string | null;
  onDateSelect: (date: string) => void;
  onMonthChange: (year: number, month: number) => void;
  className?: string;
}

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"] as const;

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay();
}

function formatDateKey(year: number, month: number, day: number): string {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function MonthCalendar({
  year,
  month,
  daySummaries,
  selectedDate,
  onDateSelect,
  onMonthChange,
  className,
}: MonthCalendarProps) {
  const [focusedDay, setFocusedDay] = React.useState<number | null>(null);
  const gridRef = React.useRef<HTMLDivElement>(null);

  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfWeek(year, month);

  const monthTotal = React.useMemo(() => {
    let count = 0;
    let amount = 0;
    for (let d = 1; d <= daysInMonth; d++) {
      const key = formatDateKey(year, month, d);
      const s = daySummaries[key];
      if (s) {
        count += s.count;
        amount += s.totalAmount;
      }
    }
    return { count, amount };
  }, [daySummaries, year, month, daysInMonth]);

  const goPrevMonth = () => {
    const m = month === 0 ? 11 : month - 1;
    const y = month === 0 ? year - 1 : year;
    onMonthChange(y, m);
  };

  const goNextMonth = () => {
    const m = month === 11 ? 0 : month + 1;
    const y = month === 11 ? year + 1 : year;
    onMonthChange(y, m);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const current = focusedDay ?? 1;
    let next = current;

    switch (e.key) {
      case "ArrowLeft":
        next = Math.max(1, current - 1);
        break;
      case "ArrowRight":
        next = Math.min(daysInMonth, current + 1);
        break;
      case "ArrowUp":
        next = current - 7 >= 1 ? current - 7 : current;
        break;
      case "ArrowDown":
        next = current + 7 <= daysInMonth ? current + 7 : current;
        break;
      case "Home":
        next = 1;
        break;
      case "End":
        next = daysInMonth;
        break;
      case "PageUp":
        e.preventDefault();
        goPrevMonth();
        return;
      case "PageDown":
        e.preventDefault();
        goNextMonth();
        return;
      case "Enter":
      case " ":
        e.preventDefault();
        onDateSelect(formatDateKey(year, month, current));
        return;
      default:
        return;
    }

    e.preventDefault();
    setFocusedDay(next);
  };

  // 날짜 셀 렌더링
  const cells: React.ReactNode[] = [];

  // 빈 셀 (첫째 주 시작 전)
  for (let i = 0; i < firstDay; i++) {
    cells.push(<div key={`empty-${i}`} role="gridcell" />);
  }

  // 날짜 셀
  for (let d = 1; d <= daysInMonth; d++) {
    const dateKey = formatDateKey(year, month, d);
    const summary = daySummaries[dateKey];
    const isSelected = dateKey === selectedDate;
    const isFocused = d === focusedDay;

    cells.push(
      <div
        key={d}
        role="gridcell"
        aria-selected={isSelected}
        tabIndex={isFocused || (focusedDay === null && d === 1) ? 0 : -1}
        onClick={() => onDateSelect(dateKey)}
        onFocus={() => setFocusedDay(d)}
        className={cn(
          "flex cursor-pointer flex-col items-center rounded-lg p-1 text-center transition-colors",
          "hover:bg-muted focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
          isSelected && "bg-primary/10 ring-primary ring-2",
        )}
      >
        <span
          className={cn(
            "text-sm font-medium",
            isSelected && "text-primary font-semibold",
          )}
        >
          {d}
        </span>
        {summary ? (
          <>
            <span className="text-primary text-[10px] font-medium">
              {summary.count}건
            </span>
            <span className="text-muted-foreground text-[10px]">
              {abbreviateAmount(summary.totalAmount)}
            </span>
          </>
        ) : null}
      </div>,
    );
  }

  // 주 단위 행 구성
  const rows: React.ReactNode[] = [];
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(
      <div key={`row-${i}`} role="row" className="grid grid-cols-7 gap-1">
        {cells.slice(i, i + 7)}
      </div>,
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      {/* 헤더: 연도/월 선택 + 네비게이션 */}
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="icon"
          onClick={goPrevMonth}
          aria-label="이전 달"
        >
          <ChevronLeftIcon className="size-5" />
        </Button>
        <div className="flex items-center gap-1">
          <Select
            value={String(year)}
            onValueChange={(v) => onMonthChange(Number(v), month)}
          >
            <SelectTrigger aria-label="연도 선택" className="h-8 w-auto gap-1 border-none bg-transparent px-2 text-base font-semibold shadow-none">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - 5 + i).map(
                (y) => (
                  <SelectItem key={y} value={String(y)}>
                    {y}년
                  </SelectItem>
                ),
              )}
            </SelectContent>
          </Select>
          <Select
            value={String(month)}
            onValueChange={(v) => onMonthChange(year, Number(v))}
          >
            <SelectTrigger aria-label="월 선택" className="h-8 w-auto gap-1 border-none bg-transparent px-2 text-base font-semibold shadow-none">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Array.from({ length: 12 }, (_, i) => i).map((m) => (
                <SelectItem key={m} value={String(m)}>
                  {m + 1}월
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={goNextMonth}
          aria-label="다음 달"
        >
          <ChevronRightIcon className="size-5" />
        </Button>
      </div>

      {/* 요일 헤더 */}
      <div role="row" className="grid grid-cols-7 gap-1">
        {WEEKDAYS.map((day) => (
          <div
            key={day}
            role="columnheader"
            className="text-muted-foreground py-1 text-center text-xs font-medium"
          >
            {day}
          </div>
        ))}
      </div>

      {/* 날짜 그리드 */}
      <div
        ref={gridRef}
        role="grid"
        aria-label={`${year}년 ${month + 1}월 달력`}
        onKeyDown={handleKeyDown}
        className="space-y-1"
      >
        {rows}
      </div>

      {/* 월 합계 */}
      <div className="border-border flex items-center justify-between rounded-lg border px-3 py-2">
        <span className="text-muted-foreground text-sm">{month + 1}월 합계</span>
        <span className="text-sm font-semibold">
          {monthTotal.count}건 · {formatPrice(monthTotal.amount)}원
        </span>
      </div>
    </div>
  );
}

export { MonthCalendar };
