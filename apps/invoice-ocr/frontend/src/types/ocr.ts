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
  // 플래그 도입(Issue #22) 이전 잡과 mock에는 없다 → optional.
  item_uncertain?: boolean;
}

export interface OcrResult {
  rows: OcrResultRow[];
  supply_sum: number;
  warp_ok: boolean;
  // 판정에 쓰인 임계값. 캘리브가 바뀐 뒤에도 과거 잡의 플래그를 그 시점 기준으로 해석하기 위한 값.
  item_conf_threshold?: number;
}

export interface OcrJobStatus {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  result?: OcrResult;
  error?: string;
}
