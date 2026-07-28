import { describe, it, expect } from "vitest";

import {
  LABEL_SOURCE,
  TOP_K,
  applyLabelSource,
  attachLabelSource,
  candidatePicked,
  type LabelSource,
} from "./label-source";

describe("candidatePicked", () => {
  it("0-based rank를 값에 담는다(0은 top1 재선택)", () => {
    expect(candidatePicked(0)).toBe("candidate_picked:0");
    expect(candidatePicked(3)).toBe("candidate_picked:3");
  });

  // 서버 화이트리스트(candidate_picked:0..TOP_K-1)를 벗어난 rank가 confirm까지
  // 도달하면 400으로 전체 저장이 실패한다 — 여기서 즉시 throw해 그 손실을 이 한 번의
  // 선택으로만 국한한다.
  it("rank가 TOP_K 이상이면 throw한다", () => {
    expect(() => candidatePicked(TOP_K)).toThrow(RangeError);
  });

  it("rank가 음수면 throw한다", () => {
    expect(() => candidatePicked(-1)).toThrow(RangeError);
  });

  it("rank가 정수가 아니면 throw한다", () => {
    expect(() => candidatePicked(1.5)).toThrow(RangeError);
  });
});

describe("applyLabelSource", () => {
  it("입력 맵을 변형하지 않고 새 맵을 돌려준다", () => {
    const prev = new Map<string, LabelSource>();
    const next = applyLabelSource(
      prev,
      "job-1/row-0",
      LABEL_SOURCE.manualTyped,
    );
    expect(prev.size).toBe(0);
    expect(next.get("job-1/row-0")).toBe("manual_typed");
  });

  it("마지막 조작이 이긴다 — 칩 → 타이핑", () => {
    let m = applyLabelSource(new Map(), "r", candidatePicked(2));
    m = applyLabelSource(m, "r", LABEL_SOURCE.manualTyped);
    expect(m.get("r")).toBe("manual_typed");
  });

  it("마지막 조작이 이긴다 — 타이핑 → 칩", () => {
    let m = applyLabelSource(new Map(), "r", LABEL_SOURCE.manualTyped);
    m = applyLabelSource(m, "r", candidatePicked(1));
    expect(m.get("r")).toBe("candidate_picked:1");
  });

  it("신규 등록 이후 다시 수정하면 그 조작의 값으로 덮인다", () => {
    let m = applyLabelSource(new Map(), "r", LABEL_SOURCE.newItemCreated);
    m = applyLabelSource(m, "r", LABEL_SOURCE.manualPicked);
    expect(m.get("r")).toBe("manual_picked");
  });
});

describe("attachLabelSource", () => {
  // 저장 대상 행의 최소 형태. crop_ref를 optional로 명시해야 weak type detection에
  // 걸리지 않는다(TS2559) — 수동 추가 행은 crop_ref 자체가 없는 같은 타입이다.
  type Row = { crop_ref?: string; name: string };

  const draftRow: Row = { crop_ref: "job-1/row-0", name: "타이어" };
  const manualRow: Row = { name: "수동추가" };

  it("미수정 초안 행의 기본값은 top1_kept다", () => {
    const [row] = attachLabelSource([draftRow], new Map());
    expect(row).toHaveProperty("label_source", "top1_kept");
  });

  it("기록된 출처가 있으면 그 값을 싣는다", () => {
    const sources = applyLabelSource(
      new Map(),
      "job-1/row-0",
      candidatePicked(0),
    );
    const [row] = attachLabelSource([draftRow], sources);
    expect(row).toHaveProperty("label_source", "candidate_picked:0");
  });

  it("crop_ref 없는 행에는 아예 필드를 붙이지 않는다", () => {
    const [row] = attachLabelSource([manualRow], new Map());
    expect(row).not.toHaveProperty("label_source");
  });

  it("원본 item 객체를 변형하지 않는다", () => {
    attachLabelSource([draftRow], new Map());
    expect(draftRow).not.toHaveProperty("label_source");
  });

  // 서버 스키마(app/schemas/ocr.py:OcrConfirmItem)는 extra="allow"라 `label_soruce` 같은
  // 오타 키를 200으로 조용히 삼키고 provenance만 유실된다. 키 이름을 여기서 고정하는 것이
  // 유일한 방어선이다 — 이 단언이 깨지면 서버가 아니라 이 테스트가 먼저 알려준다.
  it("붙이는 키 이름은 정확히 label_source다(오타 키 방어선)", () => {
    const [row] = attachLabelSource([draftRow], new Map());
    expect(Object.keys(row)).toEqual(["crop_ref", "name", "label_source"]);
  });

  // 서버 화이트리스트(LABEL_SOURCES = 고정 4종 + candidate_picked:0..TOP_K-1)를 프론트가
  // 넘지 않는지 고정한다 — 벗어나면 confirm 전체가 400이 된다. allowed 집합을 TOP_K에서
  // 파생시켜, 하드코딩된 숫자가 candidatePicked의 실제 가드 범위와 따로 노는(회귀 감지
  // 0인) 상태를 막는다.
  it("생성 가능한 값은 서버 허용 어휘를 벗어나지 않는다", () => {
    const allowed = new Set([
      "top1_kept",
      "manual_picked",
      "manual_typed",
      "new_item_created",
      ...Array.from({ length: TOP_K }, (_, rank) => `candidate_picked:${rank}`),
    ]);
    const produced = [
      ...Object.values(LABEL_SOURCE),
      ...Array.from({ length: TOP_K }, (_, rank) => candidatePicked(rank)),
    ];
    for (const value of produced) {
      expect(allowed.has(value)).toBe(true);
    }
  });
});
