import type { InvoiceItem } from "@/types/invoice";
import type { OcrItemPred, OcrResult } from "@/types/ocr";

export function rowsToItems(result: OcrResult): Partial<InvoiceItem>[] {
  return result.rows.map((row) => ({
    name: row.item_top5[0]?.label ?? "",
    unit_price: row.supply ?? 0,
    quantity: 1,
    crop_ref: row.crop_ref,
    deduction: false,
  }));
}

// OCR 메타는 InvoiceItem(저장 대상)에 섞지 않는다 — 저장 payload를 오염시키지 않기 위해
// crop_ref를 키로 한 별도 맵으로 들고 다닌다(spec §2-1).
export interface OcrItemMeta {
  candidates: OcrItemPred[];
  uncertain: boolean;
  jobId: number;
  rowIndex: number;
}

export function rowsToOcrMeta(
  result: OcrResult,
  jobId: number,
): Map<string, OcrItemMeta> {
  return new Map(
    result.rows.map((row) => [
      row.crop_ref,
      {
        candidates: row.item_top5,
        uncertain: row.item_uncertain ?? false,
        jobId,
        rowIndex: row.row_index,
      },
    ]),
  );
}
