import { describe, it, expect } from "vitest";
import { sumPerSalesperson, sumTotal } from "./aggregation";
import type { MonthGridCell } from "@/utils/calendar";
import type { Salesperson } from "@/types/salesperson";
import type { SalesRecord } from "@/types/sales-record";

function rec(id: number, spId: number, date: string, quantity: number, name: string): SalesRecord {
  return {
    id,
    salesperson_id: spId,
    work_date: date,
    quantity,
    snapshot_name: name,
  };
}

const sp = (id: number, name: string, is_active: 0 | 1 = 1): Salesperson => ({
  id,
  name,
  sort_order: id,
  is_active,
});

describe("sumPerSalesperson", () => {
  const salespeople = [sp(1, "김"), sp(2, "이"), sp(3, "박", 0)];

  const week: MonthGridCell[] = [
    { date: "2026-05-03", inMonth: true },
    { date: "2026-05-04", inMonth: true },
    { date: "2026-05-05", inMonth: true },
    { date: "2026-05-06", inMonth: true },
    { date: "2026-05-07", inMonth: true },
    { date: "2026-05-08", inMonth: true },
    { date: "2026-05-09", inMonth: true },
  ];

  it("주차 셀의 영업사원별 합계 계산", () => {
    const records = new Map<string, Map<number, SalesRecord>>([
      ["2026-05-03", new Map([[1, rec(1, 1, "2026-05-03", 1000, "김")]])],
      ["2026-05-05", new Map([[1, rec(2, 1, "2026-05-05", 2000, "김")], [2, rec(3, 2, "2026-05-05", 500, "이")]])],
    ]);
    const result = sumPerSalesperson(week, records, salespeople);
    expect(result.find((t) => t.id === 1)?.quantity).toBe(3000);
    expect(result.find((t) => t.id === 2)?.quantity).toBe(500);
    expect(result.find((t) => t.id === 3)).toBeUndefined();
  });

  it("inMonth=false 셀은 합산에서 제외", () => {
    const weekWithOuter: MonthGridCell[] = [
      { date: "2026-04-26", inMonth: false },
      ...week.slice(1),
    ];
    const records = new Map<string, Map<number, SalesRecord>>([
      ["2026-04-26", new Map([[1, rec(10, 1, "2026-04-26", 99999, "김")]])],
      ["2026-05-05", new Map([[1, rec(2, 1, "2026-05-05", 100, "김")]])],
    ]);
    const result = sumPerSalesperson(weekWithOuter, records, salespeople);
    expect(result.find((t) => t.id === 1)?.quantity).toBe(100);
  });

  it("snapshot_name 보존 (마스터 이름 변경 시점 대비)", () => {
    const records = new Map<string, Map<number, SalesRecord>>([
      ["2026-05-03", new Map([[1, rec(1, 1, "2026-05-03", 1000, "김(과거)")]])],
    ]);
    const masters = [sp(1, "김(현재)")];
    const result = sumPerSalesperson(week, records, masters);
    expect(result[0].name).toBe("김(과거)");
  });

  it("마스터에 없는 sp_id (삭제 후 historical record) 노출", () => {
    const records = new Map<string, Map<number, SalesRecord>>([
      ["2026-05-03", new Map([[99, rec(1, 99, "2026-05-03", 700, "퇴사자")]])],
    ]);
    const result = sumPerSalesperson(week, records, []);
    expect(result).toEqual([
      { id: 99, name: "퇴사자", isActive: 0, quantity: 700 },
    ]);
  });

  it("수량 0인 영업사원은 결과에서 제외", () => {
    const records = new Map<string, Map<number, SalesRecord>>();
    const result = sumPerSalesperson(week, records, salespeople);
    expect(result).toEqual([]);
  });
});

describe("sumTotal", () => {
  it("합계", () => {
    expect(
      sumTotal([
        { id: 1, name: "a", isActive: 1, quantity: 100 },
        { id: 2, name: "b", isActive: 1, quantity: 250 },
      ]),
    ).toBe(350);
  });
  it("빈 배열 → 0", () => {
    expect(sumTotal([])).toBe(0);
  });
});
