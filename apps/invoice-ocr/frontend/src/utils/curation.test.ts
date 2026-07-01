import { describe, it, expect } from "vitest";

import {
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
