import type { MonthGridCell } from "@/utils/calendar";
import type { Salesperson } from "@/types/salesperson";
import type { SalesRecord } from "@/types/sales-record";

export interface SalespersonTotal {
  id: number;
  name: string;
  isActive: 0 | 1;
  quantity: number;
}

/**
 * 주어진 날짜 셀들(주차)에 대해 영업사원별 합계를 계산.
 * - inMonth=false 셀은 제외 (전/다음 달 데이터가 행에 합산되지 않도록)
 * - snapshot_name 우선, 없으면 현재 salespeople 마스터의 name 사용
 */
export function sumPerSalesperson(
  cells: MonthGridCell[],
  recordsByDate: Map<string, Map<number, SalesRecord>>,
  salespeople: Salesperson[],
): SalespersonTotal[] {
  const totals = new Map<number, number>();
  const nameById = new Map<number, string>();

  for (const cell of cells) {
    if (!cell.inMonth) continue;
    const recs = recordsByDate.get(cell.date);
    if (!recs) continue;
    for (const [spId, r] of recs) {
      totals.set(spId, (totals.get(spId) ?? 0) + r.quantity);
      if (!nameById.has(spId)) nameById.set(spId, r.snapshot_name);
    }
  }

  const result: SalespersonTotal[] = [];
  for (const sp of salespeople) {
    if (sp.id == null) continue;
    const quantity = totals.get(sp.id) ?? 0;
    if (quantity === 0) continue;
    result.push({
      id: sp.id,
      name: nameById.get(sp.id) ?? sp.name,
      isActive: sp.is_active,
      quantity,
    });
  }

  for (const [spId, quantity] of totals) {
    if (salespeople.some((s) => s.id === spId)) continue;
    result.push({
      id: spId,
      name: nameById.get(spId) ?? "(삭제됨)",
      isActive: 0,
      quantity,
    });
  }

  return result;
}

export function sumTotal(totals: SalespersonTotal[]): number {
  return totals.reduce((s, t) => s + t.quantity, 0);
}
