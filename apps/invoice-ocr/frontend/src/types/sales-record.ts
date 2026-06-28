import type { Salesperson } from "./salesperson";

export interface SalesRecord {
  id: number;
  salesperson_id: number;
  work_date: string; // YYYY-MM-DD
  quantity: number;
  snapshot_name: string;
}

export interface MonthlySalesData {
  salespeople: Salesperson[];
  records: SalesRecord[];
}

export interface SalesRecordUpsertInput {
  salesperson_id: number;
  work_date: string;
  quantity: number;
}
