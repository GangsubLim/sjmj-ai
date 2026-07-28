/**
 * 라벨 확정 출처(label_source). OCR 초안 행에 한해 confirm payload에 실려
 * correction_json.lines[].label_source로 기록된다(오선택 사후 감사용).
 *
 * 저장 가치가 있는 것은 사후에 재계산할 수 없는 정보뿐이다 — 칩을 눌렀는지,
 * 자동완성을 거쳤는지, 신규 등록 경로를 탔는지. 품목 DB 존재 여부는 여기 담지 않는다
 * (training_pairs.canonical_label ⨝ item_suggestions.item_name으로 언제든 관측 가능).
 *
 * 허용 어휘의 SSoT는 서버(backend/app/schemas/ocr.py:LABEL_SOURCES) — 벗어난 값을 보내면
 * confirm 요청 전체가 400이 된다.
 */
export const LABEL_SOURCE = {
  top1Kept: "top1_kept",
  manualPicked: "manual_picked",
  manualTyped: "manual_typed",
  newItemCreated: "new_item_created",
} as const;

type FixedLabelSource = (typeof LABEL_SOURCE)[keyof typeof LABEL_SOURCE];
export type LabelSource = FixedLabelSource | `candidate_picked:${number}`;

/** 후보 칩 선택. rank는 0-based(0 = top1 재선택). 서버는 0..4만 허용한다. */
export function candidatePicked(rank: number): LabelSource {
  return `candidate_picked:${rank}`;
}

/** 마지막 조작이 이긴다 — 같은 crop_ref의 이전 값을 덮어쓴 새 맵을 돌려준다. */
export function applyLabelSource(
  prev: ReadonlyMap<string, LabelSource>,
  cropRef: string,
  source: LabelSource,
): Map<string, LabelSource> {
  const next = new Map(prev);
  next.set(cropRef, source);
  return next;
}

/** OCR 초안 행(crop_ref 보유)에만 label_source를 붙인다. 미기록 행의 기본값은 top1_kept. */
export function attachLabelSource<T extends { crop_ref?: string }>(
  items: readonly T[],
  sources: ReadonlyMap<string, LabelSource>,
): T[] {
  return items.map((item) =>
    item.crop_ref
      ? {
          ...item,
          label_source: sources.get(item.crop_ref) ?? LABEL_SOURCE.top1Kept,
        }
      : item,
  );
}
