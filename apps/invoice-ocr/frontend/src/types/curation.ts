// api-spec(.claude/ai-context/api-spec.json)의 Curation* 스키마를 미러한다.
// 드리프트 시 api-spec이 SSoT.

export interface CurationTop5Item {
  label: string;
  sim: number;
}

export interface CurationJobSummary {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  pair_count: number;
  unreviewed_count: number;
  created_at: string;
}

// 잡 상세 pair와 PATCH 결과가 공유하는 공통 필드.
export interface CurationPairBase {
  id: number;
  crop_ref: string;
  row_index: number;
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
  supply: number | null;
  status: "included" | "excluded";
  reviewed_at: string | null;
}

// GET /jobs/{id} 의 pair — top5 + uncertain 포함, job_id 없음.
export interface CurationJobPair extends CurationPairBase {
  top5: CurationTop5Item[];
  // 품목 top1이 result_json의 item_conf_threshold 미만이거나 후보가 없을 때 true.
  // PATCH 응답에는 없다(top5와 같은 계약 비대칭) — patchPair merge가 기존 값을 보존한다.
  uncertain: boolean;
}

// PATCH /pairs/{id} 응답 — job_id 포함, top5 없음(계약 비대칭).
export interface CurationPairPatchResult extends CurationPairBase {
  job_id: number;
}

export interface CurationJobDetail {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  warp_ok: boolean;
  created_at: string;
  pairs: CurationJobPair[];
}

export type CurationPairPatch = {
  status?: "included" | "excluded";
  canonical_label?: string;
};

export type CurationImageKind = "original" | "warped";
