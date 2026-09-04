import { describe, it, expect } from "vitest";

import {
  ROW_TYPE_CSS_COLOR,
  amountCropRects,
  availablePanels,
  isSupportedGeometryVersion,
  itemCropRects,
  quadPoints,
  rowBandRects,
} from "@/utils/stage-geometry";
import type { StageGeometry } from "@/types/curation";

const PARTIAL: StageGeometry = {
  version: 1,
  generation: 0,
  image_size: [3024, 4032],
  warp_size: [900, 2100],
  quad: [
    [10, 20],
    [30, 20],
    [30, 40],
    [10, 40],
  ],
  quad_source: "color",
  deskew_deg: 0.42,
};

const FULL: StageGeometry = {
  ...PARTIAL,
  hlines: [614, 696],
  pitch: 82,
  item_x: [96, 396],
  amount_x: [630, 896],
  rows: [
    { band: [612, 694], type: "new", item_box: [618, 690], row_index: 0 },
    { band: [694, 776], type: "cont", item_box: null, row_index: null },
    { band: [776, 858], type: "total", item_box: null, row_index: null },
  ],
};

describe("ROW_TYPE_CSS_COLOR", () => {
  it("infer_photo.COLOR의 BGR을 뒤집어 RGB로 쓴다", () => {
    // 뒤집지 않으면 cont(주황)와 total(파랑)이 서로의 색으로 그려져, 데모 HTML과
    // 다른 그림이 나오고 사람이 행 타입을 색으로 읽을 수 없다(spec §5-4).
    expect(ROW_TYPE_CSS_COLOR.new).toBe("rgb(0, 170, 0)");
    expect(ROW_TYPE_CSS_COLOR.cont).toBe("rgb(0, 130, 220)");
    expect(ROW_TYPE_CSS_COLOR.empty).toBe("rgb(190, 190, 190)");
    expect(ROW_TYPE_CSS_COLOR.total).toBe("rgb(255, 140, 0)");
  });
});

describe("isSupportedGeometryVersion", () => {
  it("아는 version만 렌더 대상이다", () => {
    expect(isSupportedGeometryVersion(FULL)).toBe(true);
    expect(isSupportedGeometryVersion({ ...FULL, version: 2 })).toBe(false);
  });
});

describe("availablePanels", () => {
  it("강등 잡의 부분 문서는 상류 패널 둘만 연다", () => {
    expect(availablePanels(PARTIAL)).toEqual(["quad", "warp"]);
  });

  it("전량 문서는 여섯 패널을 모두 연다", () => {
    expect(availablePanels(FULL)).toEqual([
      "quad",
      "warp",
      "rows",
      "rowClass",
      "itemCrop",
      "amountCrop",
    ]);
  });

  it("쿼드가 없으면 쿼드 패널도 닫는다", () => {
    expect(availablePanels({ ...PARTIAL, quad: null })).toEqual(["warp"]);
  });
});

describe("rowBandRects", () => {
  it("행 전량을 타입별 색으로 워프 폭에 걸쳐 그린다", () => {
    const rects = rowBandRects(FULL);

    expect(rects).toHaveLength(3);
    expect(rects[0]).toEqual({
      x: 0,
      y: 612,
      width: 900,
      height: 82,
      color: "rgb(0, 170, 0)",
    });
    expect(rects[2].color).toBe("rgb(255, 140, 0)");
  });

  it("행이 없는 부분 문서는 빈 배열이다", () => {
    expect(rowBandRects(PARTIAL)).toEqual([]);
  });
});

describe("itemCropRects", () => {
  it("실제로 크롭된 행만 item_x × item_box로 그린다", () => {
    const rects = itemCropRects(FULL);

    expect(rects).toEqual([
      { x: 96, y: 618, width: 300, height: 72, color: "rgb(0, 170, 0)" },
    ]);
  });
});

describe("amountCropRects", () => {
  it("블록에 속한 행(new·cont)만 그린다 — empty·total은 금액 크롭이 열리지 않는다", () => {
    const rects = amountCropRects(FULL);

    expect(rects).toHaveLength(2);
    expect(rects.map((r) => r.color)).toEqual([
      "rgb(0, 170, 0)",
      "rgb(0, 130, 220)",
    ]);
    expect(rects[0]).toEqual({
      x: 630,
      y: 612,
      width: 266,
      height: 82,
      color: "rgb(0, 170, 0)",
    });
  });

  it("box 없는 new 행은 그리지 않는다 — 그 블록의 금액 크롭 창이 연 적이 없다", () => {
    // group.block_amounts(group.py:281-286)는 `rtype != ROW_NEW or not box`인 행을
    // 건너뛴다. box 없는 new는 form_blocks에서 자기 단독 블록을 열지만 그 블록의 유일
    // 멤버가 news 루프에서 걸러지므로 read_fn이 한 번도 돌지 않는다 — 금액 크롭은 실재하지
    // 않는다(Finding 3, plan-alignment.md Important #3).
    const withBoxlessNew: StageGeometry = {
      ...FULL,
      rows: [
        ...(FULL.rows ?? []),
        { band: [858, 940], type: "new", item_box: null, row_index: null },
      ],
    };

    const rects = amountCropRects(withBoxlessNew);

    expect(rects).toHaveLength(2); // box 없는 new는 제외 — new(box 있음) + cont만
    expect(rects.some((r) => r.y === 858)).toBe(false);
  });
});

describe("quadPoints", () => {
  it("SVG polygon points 문자열을 만든다", () => {
    expect(quadPoints(PARTIAL)).toBe("10,20 30,20 30,40 10,40");
    expect(quadPoints({ ...PARTIAL, quad: null })).toBeNull();
  });
});
