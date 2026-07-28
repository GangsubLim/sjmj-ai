import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, it, expect } from "vitest";

import {
  LABEL_SOURCE,
  TOP_K,
  applyLabelSource,
  attachLabelSource,
  candidatePicked,
  type LabelSource,
} from "./label-source";

// 엔드포인트 SSoT(레포 루트 .claude/ai-context/api-spec.json)의 label_source enum.
// 백엔드 tests/test_label_source_sync.py가 backend↔spec을 잡고, 여기서 프론트↔spec을 잡는다.
// cwd에서 위로 훑어 찾는다 — jsdom 환경의 import.meta.url은 file:이 아닌 http: URL이라
// 파일 경로 기준점으로 쓸 수 없고, cwd는 실행 위치(프론트 디렉터리/레포 루트)에 따라 달라진다.
function findSpecPath(): string {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, ".claude/ai-context/api-spec.json");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error("api-spec.json을 찾지 못했습니다");
    dir = parent;
  }
}

function specLabelSourceEnum(): string[] {
  const spec = JSON.parse(readFileSync(findSpecPath(), "utf-8"));
  return spec.components.schemas.OcrConfirmRequest.properties.items.items
    .properties.label_source.enum as string[];
}

describe("TOP_K 드리프트 가드", () => {
  // TOP_K가 서버(backend TOP_K=5 → spec enum)와 어긋나면, 작을 때는 유효한 후보 클릭이
  // RangeError 경로로 빠지고 클 때는 정상 선택이 confirm 전체 400을 만든다. 짝 테스트가
  // 같은 TOP_K에서 허용 집합을 파생시키는 것만으로는(회귀 감지 0) 이 드리프트를 못 잡는다.
  it("TOP_K는 api-spec.json의 candidate_picked 개수와 일치한다", () => {
    const candidateRanks = specLabelSourceEnum().filter((v) =>
      v.startsWith("candidate_picked:"),
    );
    expect(candidateRanks).toHaveLength(TOP_K);
  });

  it("생성 가능한 모든 값이 api-spec.json enum에 들어 있다", () => {
    const allowed = new Set(specLabelSourceEnum());
    const produced: string[] = [
      ...Object.values(LABEL_SOURCE),
      ...Array.from({ length: TOP_K }, (_, rank) => candidatePicked(rank)),
    ];
    for (const value of produced) {
      expect(allowed.has(value)).toBe(true);
    }
    expect(produced).toHaveLength(allowed.size); // spec에만 있는 값도 없어야 한다
  });
});

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
