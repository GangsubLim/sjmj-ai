import { describe, it, expect } from "vitest";
import {
  getWeekCount,
  getMonthGrid,
  isToday,
  isHoliday,
  formatYYYYMMDD,
  chunkIntoWeeks,
} from "./calendar";

describe("getWeekCount", () => {
  it("2026-02 (28일, 일요일 시작) → 4주", () => {
    expect(getWeekCount(2026, 2)).toBe(4);
  });
  it("2026-05 (31일, 금요일 시작) → 6주", () => {
    expect(getWeekCount(2026, 5)).toBe(6);
  });
  it("2026-03 (31일, 일요일 시작) → 5주", () => {
    expect(getWeekCount(2026, 3)).toBe(5);
  });
});

describe("getMonthGrid", () => {
  it("2026-05 그리드: 7×6=42칸, 1일은 인덱스 5 (금)", () => {
    const grid = getMonthGrid(2026, 5);
    expect(grid).toHaveLength(42);
    expect(grid[5]).toEqual({ date: "2026-05-01", inMonth: true });
    expect(grid[0]).toEqual({ date: "2026-04-26", inMonth: false });
  });

  it("2026-02 (4주 달) → 28칸, trailing 빈 행 없음", () => {
    const grid = getMonthGrid(2026, 2);
    expect(grid).toHaveLength(28);
    expect(grid[0]).toEqual({ date: "2026-02-01", inMonth: true });
    expect(grid[27]).toEqual({ date: "2026-02-28", inMonth: true });
  });

  it("2026-03 (5주 달) → 35칸", () => {
    const grid = getMonthGrid(2026, 3);
    expect(grid).toHaveLength(35);
  });
});

describe("isToday", () => {
  it("오늘 날짜는 true", () => {
    const today = formatYYYYMMDD(new Date());
    expect(isToday(today)).toBe(true);
  });
  it("어제 날짜는 false", () => {
    expect(isToday("1999-01-01")).toBe(false);
  });
});

describe("isHoliday", () => {
  it("법정 공휴일은 true (신정/노동절/어린이날/성탄절)", () => {
    expect(isHoliday("2026-01-01")).toBe(true);
    expect(isHoliday("2026-05-01")).toBe(true);
    expect(isHoliday("2026-05-05")).toBe(true);
    expect(isHoliday("2026-12-25")).toBe(true);
  });
  it("대체공휴일도 true (삼일절/광복절/개천절)", () => {
    expect(isHoliday("2026-03-02")).toBe(true);
    expect(isHoliday("2026-08-17")).toBe(true);
    expect(isHoliday("2026-10-05")).toBe(true);
  });
  it("평일은 false", () => {
    expect(isHoliday("2026-05-04")).toBe(false);
    expect(isHoliday("2026-07-17")).toBe(false); // 제헌절(비공휴일)
  });
});

describe("formatYYYYMMDD", () => {
  it("로컬 타임존 기준 (KST)", () => {
    expect(formatYYYYMMDD(new Date(2026, 4, 1))).toBe("2026-05-01");
    expect(formatYYYYMMDD(new Date(2026, 11, 31))).toBe("2026-12-31");
  });
});

describe("chunkIntoWeeks", () => {
  it("42칸 → 7칸씩 6주", () => {
    const arr = Array.from({ length: 42 }, (_, i) => i);
    const weeks = chunkIntoWeeks(arr);
    expect(weeks).toHaveLength(6);
    expect(weeks[0]).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(weeks[5]).toEqual([35, 36, 37, 38, 39, 40, 41]);
  });

  it("size 커스텀 가능", () => {
    expect(chunkIntoWeeks([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it("빈 배열 → []", () => {
    expect(chunkIntoWeeks([])).toEqual([]);
  });
});
