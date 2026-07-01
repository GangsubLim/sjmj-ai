import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useCurationJob } from "./use-curation-job";
import { curationAPI } from "@/services/api";
import { toast } from "sonner";
import type {
  CurationJobDetail,
  CurationPairPatchResult,
} from "@/types/curation";

vi.mock("@/services/api", () => ({
  curationAPI: { getJob: vi.fn(), patchPair: vi.fn(), reviewJob: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const mockGetJob = vi.mocked(curationAPI.getJob);
const mockPatchPair = vi.mocked(curationAPI.patchPair);
const mockReviewJob = vi.mocked(curationAPI.reviewJob);
const mockToastError = vi.mocked(toast.error);

function jobDetail(): CurationJobDetail {
  return {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    warp_ok: true,
    created_at: "2026-06-30T09:00:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        reviewed_at: null,
        top5: [
          { label: "무", sim: 0.77 },
          { label: "배추", sim: 0.21 },
        ],
      },
    ],
  };
}

// cross-pair 회귀 검증용 — pair 2개.
function jobDetailMulti(): CurationJobDetail {
  const base = jobDetail();
  return {
    ...base,
    pairs: [
      base.pairs[0],
      {
        id: 9002,
        crop_ref: "128/1",
        row_index: 1,
        draft_label: "배추",
        final_label: "배추",
        canonical_label: "배추",
        supply: 5000,
        status: "included",
        reviewed_at: null,
        top5: [{ label: "배추", sim: 0.91 }],
      },
    ],
  };
}

function patchResult(
  over: Partial<CurationPairPatchResult> = {},
): CurationPairPatchResult {
  return {
    id: 9001,
    crop_ref: "128/0",
    row_index: 0,
    draft_label: "무우",
    final_label: "무",
    canonical_label: "배추",
    supply: 8000,
    status: "included",
    reviewed_at: "2026-06-30T10:00:00",
    job_id: 128,
    ...over,
  };
}

describe("useCurationJob", () => {
  beforeEach(() => vi.clearAllMocks());

  it("성공 PATCH는 응답을 merge하되 top5를 보존한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockPatchPair.mockResolvedValue({ data: patchResult() });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });

    const pair = result.current.job!.pairs[0];
    expect(pair.canonical_label).toBe("배추");
    expect(pair.top5).toHaveLength(2); // 응답에 없던 top5 보존
    expect(pair).not.toHaveProperty("job_id"); // job_id는 병합에서 제외
  });

  it("PATCH 실패 시 직전 값으로 롤백한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockPatchPair.mockRejectedValue(new Error("서버 오류"));
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.patchPair(9001, { status: "excluded" });
    });

    expect(result.current.job!.pairs[0].status).toBe("included"); // 롤백
  });

  it("연속 PATCH 중 한 pair 실패가 다른 pair 변경을 되돌리지 않는다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetailMulti() });
    mockPatchPair.mockImplementation(async (id) => {
      if (id === 9002) throw new Error("서버 오류");
      return { data: patchResult({ canonical_label: "배추" }) };
    });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });
    await act(async () => {
      await result.current.patchPair(9002, { status: "excluded" });
    });

    const pairs = result.current.job!.pairs;
    expect(pairs.find((p) => p.id === 9001)!.canonical_label).toBe("배추"); // 성공 보존
    expect(pairs.find((p) => p.id === 9002)!.status).toBe("included"); // per-pair 롤백
  });

  it("reviewJob 성공은 true를 반환한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockReviewJob.mockResolvedValue({
      data: { job_id: 128, curation_reviewed: true },
    });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let ok = false;
    await act(async () => {
      ok = await result.current.reviewJob();
    });
    expect(ok).toBe(true);
    expect(mockReviewJob).toHaveBeenCalledWith(128);
  });

  it("reviewJob 실패는 false를 반환하고 에러 토스트를 띄운다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockReviewJob.mockRejectedValue(new Error("검수 실패"));
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let ok = true;
    await act(async () => {
      ok = await result.current.reviewJob();
    });
    expect(ok).toBe(false);
    expect(mockToastError).toHaveBeenCalledWith("검수 실패");
  });

  it("getJob 실패 시 error 상태를 노출하고 loading을 내린다", async () => {
    mockGetJob.mockRejectedValue(new Error("불러오기 실패"));
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("불러오기 실패");
    expect(result.current.job).toBeNull();
  });
});
