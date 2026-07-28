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

/**
 * 후보 rank 상한(0-based, exclusive). 백엔드 `app/schemas/ocr.py`의 `TOP_K`와 동기 —
 * ml top-K가 바뀌면 여기도 함께 바꾼다. `candidatePicked`의 범위 가드와 테스트의 허용
 * 어휘 계산 양쪽이 이 값 하나에서 파생된다.
 */
export const TOP_K = 5;

/**
 * 후보 칩 선택. rank는 0-based(0 = top1 재선택). 서버는 0..TOP_K-1만 허용한다.
 *
 * 범위를 벗어난 rank는 폴백하지 않고 즉시 throw한다 — 조용히 top1_kept 등으로
 * 폴백하면 실제로 무슨 후보를 골랐는지 알 수 없는 감사 기록이 티 안 나게 저장된다.
 * 반대로 여기서 막지 않고 confirm까지 흘려보내면 서버 화이트리스트가 요청 전체를
 * 400으로 거부해 관련 없는 나머지 품목까지 통째로 저장 실패한다. throw는 이 한 번의
 * 클릭만 실패시키고(recordLabelSource 미호출, 이전 값 유지) 그 손실 범위를 최소화한다.
 */
export function candidatePicked(rank: number): LabelSource {
  if (!Number.isInteger(rank) || rank < 0 || rank >= TOP_K) {
    throw new RangeError(
      `candidatePicked: rank(${rank})는 0..${TOP_K - 1} 범위를 벗어났습니다.`,
    );
  }
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

/**
 * OCR 초안 행(crop_ref 보유)에만 label_source를 붙인다. 미기록 행의 기본값은 top1_kept.
 *
 * 반환 타입에 `label_source?`를 명시한다 — 실제로는 T[]가 아니라 일부 행에
 * label_source가 실린 객체를 돌려주므로, 타입이 그 사실을 감추면 소비 측에서 필드
 * 나열식으로 payload를 재구성할 때 label_source가 타입 에러 없이 조용히 빠질 수 있다.
 */
export function attachLabelSource<T extends { crop_ref?: string }>(
  items: readonly T[],
  sources: ReadonlyMap<string, LabelSource>,
): (T & { label_source?: LabelSource })[] {
  return items.map((item) =>
    item.crop_ref
      ? {
          ...item,
          label_source: sources.get(item.crop_ref) ?? LABEL_SOURCE.top1Kept,
        }
      : item,
  );
}
