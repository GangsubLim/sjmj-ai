export interface MonthGridCell {
  date: string; // YYYY-MM-DD (로컬)
  inMonth: boolean;
}

export function formatYYYYMMDD(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function getWeekCount(year: number, month: number): number {
  const first = new Date(year, month - 1, 1);
  const last = new Date(year, month, 0); // last day of month
  const startDow = first.getDay(); // 0=일
  const totalCells = startDow + last.getDate();
  return Math.ceil(totalCells / 7);
}

export function getMonthGrid(year: number, month: number): MonthGridCell[] {
  const first = new Date(year, month - 1, 1);
  const startDow = first.getDay();
  const gridStart = new Date(year, month - 1, 1 - startDow);
  const totalCells = getWeekCount(year, month) * 7;
  const cells: MonthGridCell[] = [];
  for (let i = 0; i < totalCells; i++) {
    const d = new Date(gridStart);
    d.setDate(gridStart.getDate() + i);
    cells.push({
      date: formatYYYYMMDD(d),
      inMonth: d.getMonth() === month - 1,
    });
  }
  return cells;
}

export function isToday(dateStr: string): boolean {
  return dateStr === formatYYYYMMDD(new Date());
}

/** 2026년 대한민국 관공서 공휴일(빨간 날) — 대체공휴일 포함, 출처: 우주항공청 2026 월력요항 */
const HOLIDAYS_2026 = new Set<string>([
  "2026-01-01", // 신정
  "2026-02-16", // 설날 연휴
  "2026-02-17", // 설날
  "2026-02-18", // 설날 연휴
  "2026-03-01", // 삼일절
  "2026-03-02", // 대체공휴일(삼일절)
  "2026-05-01", // 노동절 (2026.5.1 관공서 공휴일 지정, 구 근로자의 날)
  "2026-05-05", // 어린이날
  "2026-05-24", // 부처님 오신날
  "2026-05-25", // 대체공휴일(부처님 오신날)
  "2026-06-03", // 제9회 전국동시지방선거
  "2026-06-06", // 현충일
  "2026-08-15", // 광복절
  "2026-08-17", // 대체공휴일(광복절)
  "2026-09-24", // 추석 연휴
  "2026-09-25", // 추석
  "2026-09-26", // 추석 연휴
  "2026-10-03", // 개천절
  "2026-10-05", // 대체공휴일(개천절)
  "2026-10-09", // 한글날
  "2026-12-25", // 성탄절
]);

/** 공휴일(빨간 날) 여부. 현재 2026년만 지원. */
export function isHoliday(dateStr: string): boolean {
  return HOLIDAYS_2026.has(dateStr);
}

export function chunkIntoWeeks<T>(arr: T[], size = 7): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}
