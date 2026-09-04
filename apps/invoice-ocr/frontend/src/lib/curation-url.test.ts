import { describe, it, expect } from "vitest";
import { jobListUrl, jobDetailUrl, parseRowDelta } from "./curation-url";

describe("jobListUrl", () => {
  it("page=1이면 쿼리를 붙이지 않는다", () => {
    expect(jobListUrl("/curation", 1)).toBe("/curation");
  });

  it("page>1이면 ?page=를 붙인다", () => {
    expect(jobListUrl("/curation/pending", 3)).toBe("/curation/pending?page=3");
  });
});

describe("jobDetailUrl", () => {
  it("page=1이면 쿼리 없는 상세 URL을 만든다", () => {
    expect(jobDetailUrl("/curation", 128, 1)).toBe("/curation/128");
  });

  it("page>1이면 상세 URL에 그 잡이 속한 페이지를 붙인다", () => {
    expect(jobDetailUrl("/curation/pending", 11, 3)).toBe(
      "/curation/pending/11?page=3",
    );
  });
});

describe("행 증감 필터 보존", () => {
  it("목록 URL이 필터를 쿼리에 싣는다", () => {
    expect(jobListUrl("/curation", 1, { rowDelta: true })).toBe(
      "/curation?row_delta=true",
    );
  });

  it("page와 필터가 함께 있으면 둘 다 싣는다", () => {
    expect(jobListUrl("/curation", 3, { rowDelta: true })).toBe(
      "/curation?page=3&row_delta=true",
    );
  });

  it("상세 URL도 필터를 보존한다", () => {
    expect(jobDetailUrl("/curation", 128, 2, { rowDelta: true })).toBe(
      "/curation/128?page=2&row_delta=true",
    );
  });

  it("필터가 꺼져 있으면 쿼리 모양이 도입 이전과 같다", () => {
    expect(jobListUrl("/curation", 1, { rowDelta: false })).toBe("/curation");
    expect(jobDetailUrl("/curation", 128, 1)).toBe("/curation/128");
  });
});

describe("parseRowDelta", () => {
  it("정확히 'true'만 켜짐으로 본다", () => {
    expect(parseRowDelta("true")).toBe(true);
    expect(parseRowDelta("1")).toBe(false);
    expect(parseRowDelta(null)).toBe(false);
  });
});
