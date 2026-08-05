import { describe, it, expect } from "vitest";

import {
  curationJobState,
  isLabelCorrected,
  isLabelRenormalized,
  isPairChanged,
} from "./curation";

const base = { draft_label: "무", final_label: "무", canonical_label: "무" };

describe("curation 변경 강조 판정", () => {
  it("draft≠final 이면 인식 교정", () => {
    expect(
      isLabelCorrected({ ...base, draft_label: "무우", final_label: "무" }),
    ).toBe(true);
    expect(isLabelCorrected(base)).toBe(false);
  });

  it("final≠canonical 이면 재정규화", () => {
    expect(isLabelRenormalized({ ...base, canonical_label: "배추" })).toBe(
      true,
    );
    expect(isLabelRenormalized(base)).toBe(false);
  });

  it("둘 중 하나라도 변경이면 changed", () => {
    expect(isPairChanged(base)).toBe(false);
    expect(isPairChanged({ ...base, canonical_label: "배추" })).toBe(true);
  });
});

describe("curationJobState 3-state 판별", () => {
  it("한 번도 검수 안 한 잡은 unreviewed", () => {
    expect(
      curationJobState({
        curation_reviewed: false,
        curation_reviewed_at: null,
      }),
    ).toBe("unreviewed");
  });

  it("검수됐다가 해제된 잡은 needs_recheck", () => {
    expect(
      curationJobState({
        curation_reviewed: false,
        curation_reviewed_at: "2026-06-30T08:30:00",
      }),
    ).toBe("needs_recheck");
  });

  it("검수 완료 상태면 reviewed — 첫 검수 시각 유무와 무관하다", () => {
    expect(
      curationJobState({
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      }),
    ).toBe("reviewed");
    // migration_011 이전에 검수된 잡은 백필이 실패해 시각이 NULL일 수 있다(spec §4.1).
    // 그래도 게이트가 서 있으면 "검수됨"이다 — 판별 순서가 뒤집히면 이 케이스가 깨진다.
    expect(
      curationJobState({
        curation_reviewed: true,
        curation_reviewed_at: null,
      }),
    ).toBe("reviewed");
  });
});
