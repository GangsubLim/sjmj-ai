export interface Item {
  id?: number;
  item_name: string;
  default_unit_price?: number;
  category?: "oil" | "tires" | "parts" | "labor";
  notes?: string;
  usage_count?: number;
  last_used?: string;
  created_at?: string;
  updated_at?: string;
}
