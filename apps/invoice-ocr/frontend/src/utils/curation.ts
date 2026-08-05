import type { CurationJobSummary } from "@/types/curation";

interface LabelTriplet {
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
}

// 인식 교정: 원시 OCR(draft)과 정제 결과(final)가 다르다.
export function isLabelCorrected(
  pair: Pick<LabelTriplet, "draft_label" | "final_label">,
): boolean {
  return pair.draft_label !== pair.final_label;
}

// 재정규화: 정제 결과(final)와 큐레이터가 정한 정규 라벨(canonical)이 다르다.
export function isLabelRenormalized(
  pair: Pick<LabelTriplet, "final_label" | "canonical_label">,
): boolean {
  return pair.final_label !== pair.canonical_label;
}

export function isPairChanged(pair: LabelTriplet): boolean {
  return isLabelCorrected(pair) || isLabelRenormalized(pair);
}

export type CurationJobState = "unreviewed" | "needs_recheck" | "reviewed";

// 잡 단위 게이트의 3-state 판별을 한 곳에 모은다 — 목록 뱃지와 상세 배너가 공유한다.
// 커버리지 include가 utils/hooks/stores만이라, 이 판별이 화면 컴포넌트에 있으면 계상되지 않는다.
export function curationJobState(
  job: Pick<CurationJobSummary, "curation_reviewed" | "curation_reviewed_at">,
): CurationJobState {
  if (job.curation_reviewed) return "reviewed";
  return job.curation_reviewed_at === null ? "unreviewed" : "needs_recheck";
}

// 상태 이름도 판별과 같이 한 곳에서 소유한다 — 목록 뱃지와 상세 배너가 같은 상태를
// 다른 이름("재검수 필요"/"재확인 필요")으로 부르던 드리프트를 구조적으로 막는다.
// 어휘는 ADR 0004와 백엔드·타입 주석이 쓰는 "재검수 필요"에 맞춘다.
export const CURATION_STATE_LABELS: Record<CurationJobState, string> = {
  unreviewed: "● 미검수",
  needs_recheck: "↺ 재검수 필요",
  reviewed: "✓ 검수됨",
};
