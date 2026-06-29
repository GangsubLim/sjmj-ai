import type { InvoiceItem } from "@/types/invoice";
import type { OcrResult } from "@/types/ocr";

export function rowsToItems(result: OcrResult): Partial<InvoiceItem>[] {
  return result.rows.map((row) => ({
    name: row.item_top5[0]?.label ?? "",
    unit_price: row.supply ?? 0,
    quantity: 1,
    crop_ref: row.crop_ref,
    deduction: false,
  }));
}
