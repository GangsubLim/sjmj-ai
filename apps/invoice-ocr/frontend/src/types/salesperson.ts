export interface Salesperson {
  id?: number;
  name: string;
  sort_order: number;
  is_active: 0 | 1;
  created_at?: string;
  updated_at?: string;
}

export type SalespersonInput = Pick<Salesperson, "name" | "sort_order">;
