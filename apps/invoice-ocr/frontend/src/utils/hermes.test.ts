import { describe, it, expect } from "vitest";
import {
  HERMES_MISMATCH_LABELS,
  HERMES_STATUS_LABELS,
  formatAmount,
  formatRate,
  hermesEntryUrl,
  hermesListUrl,
  parseHermesStatus,
} from "./hermes";

describe("parseHermesStatus", () => {
  it("선언된 3종만 필터로 인정한다", () => {
    expect(parseHermesStatus("match")).toBe("match");
    expect(parseHermesStatus("mismatch")).toBe("mismatch");
    expect(parseHermesStatus("deleted")).toBe("deleted");
  });

  it("미지 값·null은 필터 없음으로 접는다", () => {
    // 서버가 400을 내는 값을 프론트가 만들어 보내지 않는다.
    expect(parseHermesStatus("MATCH")).toBeNull();
    expect(parseHermesStatus("unknown")).toBeNull();
    expect(parseHermesStatus(null)).toBeNull();
  });
});

describe("formatRate", () => {
  it("백분율 소수 첫째자리로 표시한다", () => {
    expect(formatRate(0.8333333333333334, 6)).toBe("83.3%");
    expect(formatRate(1, 5)).toBe("100.0%");
  });

  it("분모 0이면 — 를 낸다", () => {
    // 서버는 분모 0에서 0.0을 준다 — 그걸 0.0%로 그리면 '전부 틀렸다'로 읽힌다.
    expect(formatRate(0, 0)).toBe("—");
  });
});

describe("formatAmount", () => {
  it("천단위 구분 기호를 넣는다", () => {
    expect(formatAmount(165000)).toBe("165,000");
    expect(formatAmount(-10000)).toBe("-10,000");
  });

  it("값 없음과 0을 구분한다", () => {
    expect(formatAmount(null)).toBe("—");
    expect(formatAmount(0)).toBe("0");
  });
});

describe("URL 조립", () => {
  it("1페이지·필터 없음이면 쿼리를 붙이지 않는다", () => {
    expect(hermesListUrl(1, null)).toBe("/hermes");
    expect(hermesEntryUrl(42, 1, null)).toBe("/hermes/42");
  });

  it("페이지와 상태 필터를 함께 싣는다", () => {
    expect(hermesListUrl(3, "mismatch")).toBe("/hermes?page=3&status=mismatch");
    expect(hermesEntryUrl(42, 3, "mismatch")).toBe(
      "/hermes/42?page=3&status=mismatch",
    );
  });

  it("필터만 있으면 page는 생략한다", () => {
    expect(hermesListUrl(1, "deleted")).toBe("/hermes?status=deleted");
  });
});

describe("라벨", () => {
  it("상태 3종과 대조 축 5종 라벨을 모두 소유한다", () => {
    expect(Object.keys(HERMES_STATUS_LABELS)).toHaveLength(3);
    expect(Object.keys(HERMES_MISMATCH_LABELS)).toHaveLength(5);
  });
});
