import {
  STAGE_GEOMETRY_VERSION,
  type StageGeometry,
  type StageGeometryRowType,
} from "@/types/curation";

/**
 * 행 타입 배색 — 워커 데모 오버레이(handwriting/infer_photo.py:56-61 COLOR)와 같은 그림.
 *
 * ⚠️ 그쪽은 **OpenCV BGR 튜플**이다. 그대로 옮기면 cont(주황)와 total(파랑)이 서로의
 * 색으로 그려져, 사람이 행 타입을 색으로 읽을 수 없게 된다 — 여기서 순서를 뒤집는다.
 */
export const ROW_TYPE_CSS_COLOR: Record<StageGeometryRowType, string> = {
  new: "rgb(0, 170, 0)", // BGR (0, 170, 0)
  cont: "rgb(0, 130, 220)", // BGR (220, 130, 0)
  empty: "rgb(190, 190, 190)", // BGR (190, 190, 190)
  total: "rgb(255, 140, 0)", // BGR (0, 140, 255)
};

/** 계약 밖 row type 방어 — 백엔드는 스키마를 검증하지 않으므로 조용한 undefined 대신 눈에 띄는 색으로 닫는다. */
const UNKNOWN_ROW_TYPE_COLOR = "rgb(255, 0, 255)";

function rowTypeColor(type: StageGeometryRowType): string {
  return (
    (ROW_TYPE_CSS_COLOR as Record<string, string>)[type] ??
    UNKNOWN_ROW_TYPE_COLOR
  );
}

/** 아는 계약 버전인지 — 모르는 version이면 화면이 패널 대신 안내 문구로 닫는다. */
export function isSupportedGeometryVersion(g: StageGeometry): boolean {
  return g.version === STAGE_GEOMETRY_VERSION;
}

export type StagePanelId =
  | "quad"
  | "warp"
  | "rows"
  | "rowClass"
  | "itemCrop"
  | "amountCrop";

/** 패널 라벨 — spec §2가 정한 번호를 그대로 붙여 단계 대조가 눈으로 되게 한다. */
export const STAGE_PANEL_LABELS: Record<StagePanelId, string> = {
  quad: "② 쿼드",
  warp: "③ 워프 + deskew",
  rows: "⑤ 행검출",
  rowClass: "⑥ 행 분류",
  itemCrop: "⑦ 품목 크롭",
  amountCrop: "⑧ 금액 크롭",
};

/**
 * 이 문서로 실제로 그릴 수 있는 패널들 — 부분 문서는 상류만 연다.
 *
 * 워커가 도달한 만큼만 기록하므로 부재가 곧 "여기서 멈췄다"는 사실이다. 키가 없는 패널을
 * 빈 상태로 열어 두면 "그렸는데 아무것도 없다"로 읽혀 강등과 정상 공백이 섞인다.
 */
export function availablePanels(g: StageGeometry): StagePanelId[] {
  const panels: StagePanelId[] = [];
  if (g.quad) panels.push("quad");
  panels.push("warp");
  if (g.hlines) panels.push("rows");
  if (g.rows) panels.push("rowClass");
  if (g.rows && g.item_x) panels.push("itemCrop");
  if (g.rows && g.amount_x) panels.push("amountCrop");
  return panels;
}

export interface OverlayRect {
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
}

/** ⑥ 행 분류 — 행 전량을 타입별 색으로, 워프 폭에 걸쳐 그린다. */
export function rowBandRects(g: StageGeometry): OverlayRect[] {
  const [warpWidth] = g.warp_size;
  return (g.rows ?? []).map((r) => ({
    x: 0,
    y: r.band[0],
    width: warpWidth,
    height: r.band[1] - r.band[0],
    color: rowTypeColor(r.type),
  }));
}

/** ⑦ 품목 크롭 — 실제로 잘린 행만. row_index가 null인 행은 크롭 자체가 없다. */
export function itemCropRects(g: StageGeometry): OverlayRect[] {
  const itemX = g.item_x;
  if (!itemX) return [];
  return (g.rows ?? [])
    .filter((r) => r.item_box !== null)
    .map((r) => ({
      x: itemX[0],
      y: r.item_box![0],
      width: itemX[1] - itemX[0],
      height: r.item_box![1] - r.item_box![0],
      color: rowTypeColor(r.type),
    }));
}

/**
 * ⑧ 금액 크롭 — 실제로 금액 크롭 창이 열린 행만 그린다.
 *
 * group.block_amounts(group.py:281-286)는 `rtype != ROW_NEW or not box`인 행을 건너뛴다 —
 * box 없는 new는 자기 단독 블록의 유일 멤버라 그 블록의 read_fn이 한 번도 돌지 않는다.
 * item_box는 실제로 크롭된 행에만 부여되므로(new + box 있음) 그것이 정확히 "금액 크롭이
 * 열린 행"이다. orphan cont(new 없는 cont)는 과포함으로 남긴다 — read_fn이 돌지 않는
 * 이상신호라 진단 패널이 보여줄 값어치가 있다(의도적 보존).
 */
export function amountCropRects(g: StageGeometry): OverlayRect[] {
  const amountX = g.amount_x;
  if (!amountX) return [];
  return (g.rows ?? [])
    .filter(
      (r) => r.type === "cont" || (r.type === "new" && r.item_box !== null),
    )
    .map((r) => ({
      x: amountX[0],
      y: r.band[0],
      width: amountX[1] - amountX[0],
      height: r.band[1] - r.band[0],
      color: rowTypeColor(r.type),
    }));
}

/** ② 쿼드 — SVG polygon의 points 문자열. 원본 좌표계다(image_size 기준). */
export function quadPoints(g: StageGeometry): string | null {
  if (!g.quad) return null;
  return g.quad.map(([x, y]) => `${x},${y}`).join(" ");
}
