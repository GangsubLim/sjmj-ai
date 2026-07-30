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

  it("status를 excluded로 바꾸면 exclusion_reason을 서버처럼 NULL로 파생 갱신한다", async () => {
    // 시드 8001(job 127)은 status=included, exclusion_reason="blank_crop"에서 출발
    // (curation_repository.update_pair의 파생 쓰기 미러 — ADR 0006).
    const { data } = await mockCurationAPI.patchPair(8001, {
      status: "excluded",
    });

    expect(data.exclusion_reason).toBeNull();
  });

  it("포함 방향 PATCH는 기존 exclusion_reason을 지우지 않는다", async () => {
    // 시드 8002(job 127)는 status=excluded, exclusion_reason="blank_crop"에서 출발.
    const { data } = await mockCurationAPI.patchPair(8002, {
      status: "included",
    });

    expect(data.exclusion_reason).toBe("blank_crop");
  });
});
