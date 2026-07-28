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
// crop_ref를 키로 한 별도 맵으로 들고 다닌다(spec §2-1, docs/work/2026-07/2026-07-28-ocr-candidate-selection/spec.md
// — git 비추적, 없으면 Issue #22를 본다).
// candidates/반환 Map은 useOcrInfer의 React state와 참조를 공유한다 — readonly로 선언해
// 소비자의 in-place mutate(예: candidates.sort())로 state가 몰래 바뀌는 것을 컴파일 타임에 막는다.
export interface OcrItemMeta {
  candidates: readonly OcrItemPred[];
  uncertain: boolean;
  jobId: number;
  rowIndex: number;
}

export function rowsToOcrMeta(
  result: OcrResult,
  jobId: number,
): ReadonlyMap<string, OcrItemMeta> {
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
