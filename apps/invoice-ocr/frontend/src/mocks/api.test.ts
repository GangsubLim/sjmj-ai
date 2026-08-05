import { describe, it, expect, beforeEach, vi } from "vitest";

// Issue #52 — mock은 서버 파생 쓰기(release_gate/COALESCE)를 미러하는 계약물이다.
// curationJobs는 mocks/api.ts의 모듈 전역 가변 상태라, 테스트마다 모듈을 리셋해
// 시드를 새로 로드한다 — 실행 순서에 의존하지 않고 각 테스트가 시드 원본에서 출발한다.
let mockCurationAPI: typeof import("./api").mockCurationAPI;

beforeEach(async () => {
  vi.resetModules();
  ({ mockCurationAPI } = await import("./api"));
});

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

  it("reviewed_at을 서버처럼 NULL로 되돌리고, 소속 잡 게이트를 해제하되 curation_reviewed_at은 지우지 않는다(needs_recheck 도달)", async () => {
    // 잡 127은 시드에서 curation_reviewed=true(검수 완료 상태)로 출발한다.
    const before = await mockCurationAPI.getJob(127);
    expect(before.data.curation_reviewed).toBe(true);

    const { data } = await mockCurationAPI.patchPair(8001, {
      canonical_label: "당근",
    });
    expect(data.reviewed_at).toBeNull();

    const after = await mockCurationAPI.getJob(127);
    expect(after.data.curation_reviewed).toBe(false);
    expect(after.data.curation_reviewed_at).not.toBeNull();
  });

  it("PATCH 응답에 job_curation_reviewed: false를 싣는다(검수완료 잡의 게이트 해제를 반영)", async () => {
    // 잡 127은 시드에서 curation_reviewed=true로 출발한다 — 응답 상수(false)와 실제
    // 스토어 전이(true→false)를 함께 검증해, "항상 false를 반환"하는 하드코딩과
    // "잡 상태를 읽어 false"를 구별한다.
    const before = await mockCurationAPI.getJob(127);
    expect(before.data.curation_reviewed).toBe(true);

    const { data } = await mockCurationAPI.patchPair(8001, {
      canonical_label: "당근",
    });
    expect(data.job_curation_reviewed).toBe(false);

    const after = await mockCurationAPI.getJob(127);
    expect(after.data.curation_reviewed).toBe(false);
  });
});

describe("mockCurationAPI.reviewJob — COALESCE 미러", () => {
  it("최초 검수 시 curation_reviewed_at을 채우고 재확정 시에는 유지한다", async () => {
    // 잡 128은 시드에서 curation_reviewed_at=null(한 번도 검수 안 함)로 출발한다.
    await mockCurationAPI.reviewJob(128);
    const firstPass = await mockCurationAPI.getJob(128);
    const firstStamp = firstPass.data.curation_reviewed_at;
    expect(firstStamp).not.toBeNull();

    await mockCurationAPI.reviewJob(128);
    const secondPass = await mockCurationAPI.getJob(128);
    expect(secondPass.data.curation_reviewed_at).toBe(firstStamp);
  });
});
