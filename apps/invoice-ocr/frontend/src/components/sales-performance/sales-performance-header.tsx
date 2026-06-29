import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Props {
  year: number;
  month: number;
  isCapturing: boolean;
  onYearChange: (year: number) => void;
  onMonthChange: (month: number) => void;
  onDownload: () => void;
}

const YEAR_RANGE = 5;

export function SalesPerformanceHeader({
  year,
  month,
  isCapturing,
  onYearChange,
  onMonthChange,
  onDownload,
}: Props) {
  const currentYear = new Date().getFullYear();
  const years = Array.from(
    { length: YEAR_RANGE * 2 + 1 },
    (_, i) => currentYear - YEAR_RANGE + i,
  );
  const months = Array.from({ length: 12 }, (_, i) => i + 1);

  return (
    <header className="flex items-center justify-between gap-4 py-4">
      <div className="flex items-center gap-2">
        <Select
          value={String(year)}
          onValueChange={(v) => onYearChange(Number(v))}
        >
          <SelectTrigger aria-label="연도 선택" className="min-w-[110px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {years.map((y) => (
              <SelectItem key={y} value={String(y)}>
                {y}년
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={String(month)}
          onValueChange={(v) => onMonthChange(Number(v))}
        >
          <SelectTrigger aria-label="월 선택" className="min-w-[90px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {months.map((m) => (
              <SelectItem key={m} value={String(m)}>
                {m}월
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Button onClick={onDownload} disabled={isCapturing}>
        {isCapturing ? (
          <Loader2 className="mr-1 size-4 animate-spin" />
        ) : (
          <Download className="mr-1 size-4" />
        )}
        {isCapturing ? "PNG 생성 중…" : "PNG 저장"}
      </Button>
    </header>
  );
}
