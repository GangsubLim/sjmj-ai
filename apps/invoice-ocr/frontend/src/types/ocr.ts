export interface OcrItemPred {
  label: string;
  sim: number;
}

export interface OcrResultRow {
  row_index: number;
  crop_ref: string;
  item_top5: OcrItemPred[];
  supply: number | null;
  amount_raw: string;
}

export interface OcrResult {
  rows: OcrResultRow[];
  supply_sum: number;
  warp_ok: boolean;
}

export interface OcrJobStatus {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  result?: OcrResult;
  error?: string;
}
