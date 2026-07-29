import { describe, it, expect } from "vitest";

import { mockCurationAPI } from "./api";

describe("mockCurationAPI.patchPair", () => {
  it("실제 백엔드 PATCH 응답과 동일하게 uncertain을 포함하지 않는다", async () => {
    const { data } = await mockCurationAPI.patchPair(9001, {
      canonical_label: "배추",
    });

    expect(data).not.toHaveProperty("uncertain");
  });

  it("실제 백엔드 PATCH 응답과 동일하게 top5를 포함하지 않는다", async () => {
    const { data } = await mockCurationAPI.patchPair(9002, {
      canonical_label: "무",
    });

    expect(data).not.toHaveProperty("top5");
  });
});
