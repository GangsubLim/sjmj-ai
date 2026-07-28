import { describe, it, expect } from "vitest";
import { curationImageUrl, ocrCropUrl } from "./api";

describe("이미지 URL 빌더", () => {
  it("imageUrl은 잡/kind 경로를 조립한다", () => {
    const url = curationImageUrl(128, "warped");
    expect(url).toContain("/api/");
    expect(url.endsWith("/curation/jobs/128/image/warped")).toBe(true);
  });

  it("cropUrl은 ocr 네임스페이스로 잡/행 경로를 조립한다", () => {
    const url = ocrCropUrl(128, 3);
    expect(url.endsWith("/ocr/jobs/128/crop/3")).toBe(true);
  });

  it("original kind도 처리한다", () => {
    expect(
      curationImageUrl(5, "original").endsWith(
        "/curation/jobs/5/image/original",
      ),
    ).toBe(true);
  });
});
