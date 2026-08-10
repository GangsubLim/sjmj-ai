import { describe, it, expect } from "vitest";
import { jobListUrl, jobDetailUrl } from "./curation-url";

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
