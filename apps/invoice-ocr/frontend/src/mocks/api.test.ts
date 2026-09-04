import { describe, it, expect, beforeEach, vi } from "vitest";

// Issue #52 — mock은 서버 파생 쓰기(release_gate/COALESCE)를 미러하는 계약물이다.
// curationJobs는 mocks/api.ts의 모듈 전역 가변 상태라, 테스트마다 모듈을 리셋해
// 시드를 새로 로드한다 — 실행 순서에 의존하지 않고 각 테스트가 시드 원본에서 출발한다.
let mockCurationAPI: typeof import("./api").mockCurationAPI;

beforeEach(async () => {
  vi.resetModules();
  ({ mockCurationAPI } = await import("./api"));
});

// 세대 토큰은 mock도 대조한다(서버 미러) — 각 호출은 그 쌍이 속한 잡의 현재 토큰을 싣는다.
async function tokenOfJob(jobId: number): Promise<string> {
  return (await mockCurationAPI.getJob(jobId)).data.job_token;
}

async function tokenOfPair(pairId: number): Promise<string> {
  const { data } = await mockCurationAPI.getJobs();
  for (const summary of data) {
    const job = await mockCurationAPI.getJob(summary.job_id);
    if (job.data.pairs.some((p) => p.id === pairId)) return job.data.job_token;
  }
  throw new Error(`쌍 ${pairId}의 잡을 찾을 수 없다`);
}

describe("mockCurationAPI.patchPair", () => {
  it("실제 백엔드 PATCH 응답과 동일하게 uncertain을 포함하지 않는다", async () => {
    const { data } = await mockCurationAPI.patchPair(9001, {
      job_token: await tokenOfPair(9001),
      canonical_label: "배추",
    });

    expect(data).not.toHaveProperty("uncertain");
  });

  it("실제 백엔드 PATCH 응답과 동일하게 top5를 포함하지 않는다", async () => {
    const { data } = await mockCurationAPI.patchPair(9002, {
      job_token: await tokenOfPair(9002),
      canonical_label: "무",
    });

    expect(data).not.toHaveProperty("top5");
  });

  it("status를 excluded로 바꾸면 exclusion_reason을 서버처럼 NULL로 파생 갱신한다", async () => {
    // 시드 8001(job 127)은 status=included, exclusion_reason="blank_crop"에서 출발
    // (curation_repository.update_pair의 파생 쓰기 미러 — ADR 0006).
    const { data } = await mockCurationAPI.patchPair(8001, {
      job_token: await tokenOfPair(8001),
      status: "excluded",
    });

    expect(data.exclusion_reason).toBeNull();
  });

  it("포함 방향 PATCH는 기존 exclusion_reason을 지우지 않는다", async () => {
    // 시드 8002(job 127)는 status=excluded, exclusion_reason="blank_crop"에서 출발.
    const { data } = await mockCurationAPI.patchPair(8002, {
      job_token: await tokenOfPair(8002),
      status: "included",
    });

    expect(data.exclusion_reason).toBe("blank_crop");
  });

  it("reviewed_at을 서버처럼 NULL로 되돌리고, 소속 잡 게이트를 해제하되 curation_reviewed_at은 지우지 않는다(needs_recheck 도달)", async () => {
    // 잡 127은 시드에서 curation_reviewed=true(검수 완료 상태)로 출발한다.
    const before = await mockCurationAPI.getJob(127);
    expect(before.data.curation_reviewed).toBe(true);

    const { data } = await mockCurationAPI.patchPair(8001, {
      job_token: await tokenOfPair(8001),
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
      job_token: await tokenOfPair(8001),
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
    await mockCurationAPI.reviewJob(128, await tokenOfJob(128));
    const firstPass = await mockCurationAPI.getJob(128);
    const firstStamp = firstPass.data.curation_reviewed_at;
    expect(firstStamp).not.toBeNull();

    await mockCurationAPI.reviewJob(128, await tokenOfJob(128));
    const secondPass = await mockCurationAPI.getJob(128);
    expect(secondPass.data.curation_reviewed_at).toBe(firstStamp);
  });

  it("스탬프를 서버와 같은 naive 로컬 ISO(초 정밀도)로 만든다", async () => {
    // 백엔드는 MySQL DATETIME을 "2026-06-30T08:30:00"로 낸다. toISOString()의
    // UTC "Z"·밀리초 형태를 쓰면 mock 저장소에 서버가 만들 수 없는 값이 섞인다.
    await mockCurationAPI.reviewJob(128, await tokenOfJob(128));
    const { data } = await mockCurationAPI.getJob(128);
    expect(data.curation_reviewed_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/,
    );
    expect(data.pairs[0].reviewed_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/,
    );
  });
});

describe("mockCurationAPI 쓰기 가드 — status !== done", () => {
  // 서버는 재처리 큐에 든 잡의 PATCH·검수완료를 409로 거부한다(curation_service의
  // patch_pair·mark_reviewed). mock이 미러하지 않으면 그 경로가 mock 모드·e2e에서만
  // 통과해 실서버에서만 드러난다(job_token 대조를 미러하는 것과 같은 이유).
  it("재처리 큐에 든 잡의 쌍 PATCH를 거부한다", async () => {
    await expect(
      mockCurationAPI.patchPair(6001, {
        job_token: await tokenOfPair(6001),
        canonical_label: "배추",
      }),
    ).rejects.toThrow(/mock 409/);
  });

  it("재처리 큐에 든 잡의 검수 완료를 거부한다", async () => {
    await expect(
      mockCurationAPI.reviewJob(125, await tokenOfJob(125)),
    ).rejects.toThrow(/mock 409/);
  });

  it("done 잡의 쓰기는 그대로 통과한다", async () => {
    const { data } = await mockCurationAPI.patchPair(9001, {
      job_token: await tokenOfPair(9001),
      canonical_label: "배추",
    });
    expect(data.canonical_label).toBe("배추");
  });
});

describe("mockCurationAPI.getGeometry", () => {
  it("잡 128은 기하 문서를 반환한다", async () => {
    const { data } = await mockCurationAPI.getGeometry(128);
    expect(data.version).toBe(1);
    expect(data.generation).toBe(0);
  });

  it("기하가 없는 잡은 실패한다(404 폴백 재현)", async () => {
    await expect(mockCurationAPI.getGeometry(999999)).rejects.toThrow();
  });
});

describe("mockCurationAPI.getJobs", () => {
  it("요약에 행 증감 두 수를 싣는다", async () => {
    const { data } = await mockCurationAPI.getJobs();

    const job = data.find((j) => j.job_id === 128);
    expect(job?.rows_added).toBe(2);
    expect(job?.rows_dropped).toBe(0);
  });

  it("correction이 없는 잡은 관측 없음(null)으로 남는다", async () => {
    const { data } = await mockCurationAPI.getJobs();

    const job = data.find((j) => j.job_id === 125);
    expect(job?.rows_added).toBeNull();
    expect(job?.rows_dropped).toBeNull();
  });

  it("row_delta=true면 증감 있는 잡만·total도 함께 좁힌다", async () => {
    const { data, pagination } = await mockCurationAPI.getJobs({
      row_delta: true,
    });

    expect(data.map((j) => j.job_id).sort()).toEqual([127, 128]);
    expect(pagination.total).toBe(2);
  });
});
