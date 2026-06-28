import type { SalesRecord } from "@/types/sales-record";
import { mockSalespeople } from "./salespeople";

const SP = mockSalespeople;
const r = (
  id: number,
  spId: number,
  date: string,
  quantity: number,
): SalesRecord => ({
  id,
  salesperson_id: spId,
  work_date: date,
  quantity,
  snapshot_name: SP.find((s) => s.id === spId)!.name,
});

// 2026-05 주차별 다양성: 1주차 1명, 2주차 2명, 3주차 3명, 4주차 1명, 5주차 2명, 6주차 3명
export const mockSalesRecords: SalesRecord[] = [
  // 1주차 (May 1-2): 1명
  r(1, 1, "2026-05-01", 10),

  // 2주차 (May 3-9): 2명
  r(2, 1, "2026-05-05", 12),
  r(3, 2, "2026-05-07", 8),

  // 3주차 (May 10-16): 3명
  r(4, 1, "2026-05-11", 15),
  r(5, 2, "2026-05-13", 22),
  r(6, 3, "2026-05-15", 9),

  // 4주차 (May 17-23): 1명
  r(7, 4, "2026-05-20", 18),

  // 5주차 (May 24-30): 2명
  r(8, 1, "2026-05-26", 20),
  r(9, 5, "2026-05-28", 24),

  // 6주차 (May 31): 3명
  r(10, 2, "2026-05-31", 14),
  r(11, 3, "2026-05-31", 11),
  r(12, 5, "2026-05-31", 7),
];
