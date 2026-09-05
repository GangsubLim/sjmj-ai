import type { CurationJobStatus, CurationJobSummary } from "@/types/curation";

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

// status가 done이 아니면 서버가 쌍 PATCH·검수완료를 409로 거부한다(curation_service의
// patch_pair·mark_reviewed). 첫 저장 시도의 409에서야 상태를 알면 그때까지의 편집이
// 통째로 낭비되므로, 화면이 열리는 시점에 같은 사실을 예고한다(#86). 차단은 하지 않는다.
// 문구를 상태별로 가르는 이유: failed에 "처리가 끝난 뒤"는 사실과 다르다(영영 끝나지
// 않는다) — 백엔드 메시지 분기(#93)와 같은 근거다.
// 미지 상태는 경고 쪽으로 실패시킨다 — ocr_jobs.status는 VARCHAR라 새 상태값이 생길 수
// 있고, done이 아닌 이상 patch_pair·mark_reviewed가 409로 거부하는 것은 동일하다.
// 화이트리스트(pending|running만 배너)로 뒤집으면 그 새 상태에서 배너가 조용히 사라져
// #86이 없애려던 증상이 그대로 재발한다.
export function curationJobBlockedNotice(
  status: CurationJobStatus,
): { title: string; body: string } | null {
  if (status === "done") return null;
  if (status === "failed") {
    return {
      title: "⚠ 처리 실패",
      body: "처리에 실패한 잡입니다. 재처리를 요청해 다시 시도하세요. 지금 수정해도 저장되지 않습니다.",
    };
  }
  return {
    title: "⏳ 재처리 대기·진행 중",
    body: "재처리 큐에 든 잡입니다. 지금 수정해도 저장되지 않으니, 처리가 끝난 뒤 검수하세요.",
  };
}

// 행 증감 표시 — 방향을 갈라 쓴다. 더한 쪽은 행을 놓친 것이고 버린 쪽은 없는 행을 만든
// 것이라 같은 기준선을 정반대로 움직여야 하므로 합산하지 않는다(CONTEXT.md "행 증감").
// 원인 라벨("행검출 불량" 등)을 붙이지 않는 것도 계약이다 — 사람이 전표에 없는 항목을
// 넣은 경우가 같은 수로 섞이므로, 관측된 사실까지만 말한다.
export function rowDeltaText(
  job: Pick<CurationJobSummary, "rows_added" | "rows_dropped">,
): string {
  if (job.rows_added === null && job.rows_dropped === null) return "—";
  const added = job.rows_added === null ? "+?" : `+${job.rows_added}`;
  const dropped = job.rows_dropped === null ? "−?" : `−${job.rows_dropped}`;
  return `${added} / ${dropped}`;
}
