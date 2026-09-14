import { describe, it, expect } from "vitest";
import {
  HERMES_MISMATCH_LABELS,
  HERMES_STATUS_LABELS,
  formatAmount,
  formatRate,
  hermesEntryUrl,
  hermesListUrl,
  knowledgeVersionNote,
  parseHermesStatus,
  summarizeKnowledgeChanges,
} from "./hermes";
import type {
  HermesKnowledgeChange,
  HermesKnowledgeVersion,
} from "@/types/hermes";

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

describe("summarizeKnowledgeChanges", () => {
  const change = (
    over: Partial<HermesKnowledgeChange> = {},
  ): HermesKnowledgeChange => ({
    section: "확정 어휘",
    added: [],
    removed: [],
    moved: [],
    changed: [],
    ...over,
  });

  it("절마다 종류별 건수를 한 칩으로 접는다", () => {
    expect(
      summarizeKnowledgeChanges([
        change({
          section: "확정 어휘",
          added: [{ group: "보통(3~9회)", text: "콜드호수 (EA)" }],
          moved: [
            { text: "챔바 (EA)", from: "보통(3~9회)", to: "가끔(2회)" },
            { text: "하부 (EA)", from: "가끔(2회)", to: "보통(3~9회)" },
          ],
        }),
        change({
          section: "데이터 현황",
          changed: [
            { group: "", key: "누적 교정", before: "44건", after: "50건" },
          ],
        }),
        change({
          section: "거래처 프로필",
          added: [{ group: "", text: "a" }],
          removed: [{ group: "", text: "b" }],
        }),
      ]),
    ).toEqual(["확정 어휘 +1 ↔2", "데이터 현황 ~1", "거래처 프로필 +1 −1"]);
  });

  it("변경이 없으면 빈 목록이다", () => {
    expect(summarizeKnowledgeChanges([])).toEqual([]);
  });
});

describe("knowledgeVersionNote", () => {
  const row = (
    over: Partial<HermesKnowledgeVersion> = {},
  ): HermesKnowledgeVersion => ({
    version: 3,
    published_at: "2026-09-10T03:00:03",
    corrections_through: 25,
    rejected: null,
    changes: [],
    missing_file: false,
    ...over,
  });

  it("거부 기록은 사유를 낸다", () => {
    expect(knowledgeVersionNote(row({ rejected: "헤딩 누락" }))).toBe(
      "거부: 헤딩 누락",
    );
  });

  it("파일 부재를 초기 발행보다 먼저 판정한다", () => {
    expect(
      knowledgeVersionNote(row({ changes: null, missing_file: true })),
    ).toBe("파일 없음");
  });

  it("직전이 없는 발행은 초기 발행이다", () => {
    expect(knowledgeVersionNote(row({ version: 1, changes: null }))).toBe(
      "초기 발행",
    );
  });

  it("빈 변경은 변경 없음이다", () => {
    expect(knowledgeVersionNote(row({ changes: [] }))).toBe("변경 없음");
  });

  it("변경이 있으면 null — 칩이 대신 말한다", () => {
    expect(
      knowledgeVersionNote(
        row({
          changes: [
            {
              section: "일반화 규칙",
              added: [{ group: "", text: "x" }],
              removed: [],
              moved: [],
              changed: [],
            },
          ],
        }),
      ),
    ).toBeNull();
  });
});
