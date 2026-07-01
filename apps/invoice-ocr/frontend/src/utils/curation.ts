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
