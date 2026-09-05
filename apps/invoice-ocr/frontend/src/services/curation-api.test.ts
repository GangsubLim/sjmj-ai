import { describe, it, expect, vi, beforeEach } from "vitest";
import api, { curationImageUrl, ocrCropUrl, curationAPI } from "./api";

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

describe("curationAPI.getJobs", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("필터가 꺼져 있으면 row_delta를 아예 싣지 않는다", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      data: { success: true, data: [], pagination: null },
    });

    await curationAPI.getJobs({ page: 2, limit: 20 });

    // 키를 false로 실으면 필터 off 요청이 현행과 달라져 "회귀 0"이 깨진다.
    expect(api.get).toHaveBeenCalledWith("/curation/jobs", {
      params: { page: 2, limit: 20 },
    });
  });

  it("필터가 켜지면 row_delta=true를 싣는다", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      data: { success: true, data: [], pagination: null },
    });

    await curationAPI.getJobs({ page: 1, limit: 20, row_delta: true });

    expect(api.get).toHaveBeenCalledWith("/curation/jobs", {
      params: { page: 1, limit: 20, row_delta: true },
    });
  });
});

describe("curationAPI.getGeometry", () => {
  it("잡별 geometry 경로로 GET한다", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: { success: true, data: { version: 1 } },
    });

    await curationAPI.getGeometry(42);

    expect(get).toHaveBeenCalledWith("/curation/jobs/42/geometry");
  });
});
