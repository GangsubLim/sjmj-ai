export interface InvoiceItem {
  id?: number;
  name: string;
  quantity: number | string;
  unit_price: number | string;
  supply: number;
  vat: number;
  total: number;
  item_order: number;
  deduction: boolean;
  crop_ref?: string;
}

export interface Invoice {
  id?: number;
  document_title: string;
  issue_date: string;
  recipient: string;
  recipient2?: string;
  vehicle_no: string;
  show_stamp: boolean;
  memo?: string;
  issuer_id?: number;
  total_supply: number;
  total_vat: number;
  grand_total: number;
  items: InvoiceItem[];
  created_at?: string;
  updated_at?: string;
}

export interface InvoiceFilters {
  search?: string;
  date_from?: string;
  date_to?: string;
  sort_by?: "date" | "amount" | "company";
  sort_order?: "asc" | "desc";
  page?: number;
  limit?: number;
}
