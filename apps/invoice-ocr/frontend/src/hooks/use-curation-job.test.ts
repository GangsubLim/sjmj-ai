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
    curation_reviewed_at: null,
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
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
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
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
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
    exclusion_reason: null,
    reviewed_at: "2026-06-30T10:00:00",
    job_id: 128,
    job_curation_reviewed: false,
    ...over,
  };
}

// resolve/reject를 외부에서 제어할 수 있는 Promise — 응답 도착 순서를 시험 코드가 결정한다.
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useCurationJob", () => {
  beforeEach(() => vi.clearAllMocks());

  it("역순 응답에서 마지막 선택이 유지된다(늦은 A 응답이 B를 덮지 않는다)", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      const pA = result.current.patchPair(9001, { canonical_label: "A" });
      const pB = result.current.patchPair(9001, { canonical_label: "B" });
      dB.resolve({ data: patchResult({ canonical_label: "B" }) }); // 두 번째 요청이 먼저 도착
      dA.resolve({ data: patchResult({ canonical_label: "A" }) }); // 첫 번째 요청이 나중에 도착 → 버려져야 한다
      await pB;
      await pA;
    });

    expect(result.current.job!.pairs[0].canonical_label).toBe("B");
  });

  it("stale 요청의 실패는 이후 선택을 되돌리지 않고 토스트도 띄우지 않는다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      const pA = result.current.patchPair(9001, { canonical_label: "A" });
      const pB = result.current.patchPair(9001, { canonical_label: "B" });
      dB.resolve({ data: patchResult({ canonical_label: "B" }) });
      dA.reject(new Error("network"));
      await pB;
      await pA;
    });

    expect(result.current.job!.pairs[0].canonical_label).toBe("B");
    expect(mockToastError).not.toHaveBeenCalled();
  });

  // 롤백 기준선이 '요청 시작 시점의 로컬 값'이면 앞 요청의 옵티미스틱 값(서버에 저장된 적
  // 없는 값)으로 되돌아가 화면과 서버가 발산한다 — 서버 확정 스냅샷으로 되돌려야 한다.
  it("겹친 두 요청이 모두 실패하면 서버 확정값까지 되돌린다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    // 두 요청 사이에 렌더를 끼운다 — 실제 연속 클릭처럼 두 번째 요청이 시작될 때
    // 로컬 값은 이미 첫 요청의 옵티미스틱 값("A")이다.
    let pA!: Promise<void>;
    let pB!: Promise<void>;
    await act(async () => {
      pA = result.current.patchPair(9001, { canonical_label: "A" });
    });
    await act(async () => {
      pB = result.current.patchPair(9001, { canonical_label: "B" });
    });

    await act(async () => {
      dA.reject(new Error("network")); // stale 실패 — 조용히 버려진다
      dB.reject(new Error("network")); // 최신 실패 — 롤백 담당
      await pA;
      await pB;
    });

    // 서버는 아직 "무"(초기 확정값)다. "A"로 남으면 저장된 적 없는 값이 화면에 남는 것.
    expect(result.current.job!.pairs[0].canonical_label).toBe("무");
    expect(mockToastError).toHaveBeenCalledTimes(1);
  });

  // 늦게 온 성공도 '서버가 저장했다'는 사실이다. 확정값에 반영하지 않으면 뒤이은 최신
  // 요청의 실패가 저장된 적 있는 값을 건너뛰고 옛 값까지 되돌려 화면이 서버와 발산한다.
  it("stale 성공 뒤 최신 요청이 실패하면 서버가 저장한 값으로 롤백한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let pA!: Promise<void>;
    let pB!: Promise<void>;
    await act(async () => {
      pA = result.current.patchPair(9001, { canonical_label: "A" });
    });
    await act(async () => {
      pB = result.current.patchPair(9001, { canonical_label: "B" });
    });

    await act(async () => {
      dA.resolve({ data: patchResult({ canonical_label: "A" }) }); // stale 성공 — 서버엔 저장됨
      dB.reject(new Error("network")); // 최신 실패 — 롤백 담당
      await pA;
      await pB;
    });

    // 서버는 "A"를 들고 있다. 초기 확정값 "무"로 돌아가면 저장된 값과 화면이 발산한다.
    expect(result.current.job!.pairs[0].canonical_label).toBe("A");
    expect(mockToastError).toHaveBeenCalledTimes(1);
  });

  // 위와 같은 발산의 도착 순서만 뒤집은 경우 — 롤백으로 화면이 이미 확정값을 비추고
  // 있으므로 뒤늦게 온 성공은 덮을 선택이 없다. 여기서 멈추면 발산이 그대로 남는다.
  it("최신 요청 실패로 롤백된 뒤 도착한 stale 성공은 화면까지 반영한다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let pA!: Promise<void>;
    let pB!: Promise<void>;
    await act(async () => {
      pA = result.current.patchPair(9001, { canonical_label: "A" });
    });
    await act(async () => {
      pB = result.current.patchPair(9001, { canonical_label: "B" });
    });

    await act(async () => {
      dB.reject(new Error("network")); // 최신 실패 — 확정값("무")으로 롤백
      dA.resolve({ data: patchResult({ canonical_label: "A" }) }); // 그 뒤 도착한 stale 성공
      await pB;
      await pA;
    });

    expect(result.current.job!.pairs[0].canonical_label).toBe("A");
    expect(mockToastError).toHaveBeenCalledTimes(1); // 실패 토스트는 그대로 1회
  });

  // stale 성공을 확정값에 반영하되 '발행 순서'로만 받아들여야 한다 — 뒤늦게 도착한 옛
  // 성공이 더 최신 확정을 덮으면 롤백 기준선이 과거로 후퇴한다.
  it("뒤늦게 도착한 옛 성공은 더 최신 확정값을 덮지 않는다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise)
      .mockRejectedValueOnce(new Error("network"));

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let pA!: Promise<void>;
    let pB!: Promise<void>;
    await act(async () => {
      pA = result.current.patchPair(9001, { canonical_label: "A" });
    });
    await act(async () => {
      pB = result.current.patchPair(9001, { canonical_label: "B" });
    });

    await act(async () => {
      dB.resolve({ data: patchResult({ canonical_label: "B" }) }); // 최신 성공이 먼저
      dA.resolve({ data: patchResult({ canonical_label: "A" }) }); // 옛 성공이 나중
      await pB;
      await pA;
    });

    // 세 번째 요청을 실패시켜 롤백 기준선(=확정값)이 무엇인지 관찰한다.
    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "C" });
    });

    expect(result.current.job!.pairs[0].canonical_label).toBe("B");
  });

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
    expect(mockToastError).toHaveBeenCalled(); // 정상(최신) 실패 경로는 토스트를 띄운다
  });

  it("언마운트 후 도착한 in-flight 응답은 무시하고 롤백·토스트도 없다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    const d = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair.mockReturnValueOnce(d.promise);

    const { result, unmount } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let patchPromise!: Promise<void>;
    act(() => {
      patchPromise = result.current.patchPair(9001, { canonical_label: "A" });
    });

    unmount();

    await act(async () => {
      d.reject(new Error("network"));
      await patchPromise;
    });

    expect(mockToastError).not.toHaveBeenCalled();
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

  it("PATCH 성공은 잡 게이트를 해제한다(검수 완료 버튼 재활성화 경로)", async () => {
    mockGetJob.mockResolvedValue({
      data: {
        ...jobDetail(),
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      },
    });
    mockPatchPair.mockResolvedValue({ data: patchResult() });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.job).not.toBeNull());

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });

    expect(result.current.job!.curation_reviewed).toBe(false);
    // 첫 검수 시각은 서버가 지우지 않으므로 로컬에서도 유지된다 → "재검수 필요"로 판별된다.
    expect(result.current.job!.curation_reviewed_at).toBe(
      "2026-06-30T08:30:00",
    );
  });

  it("job_curation_reviewed는 pair 객체에 새지 않는다", async () => {
    mockGetJob.mockResolvedValue({ data: jobDetail() });
    mockPatchPair.mockResolvedValue({ data: patchResult() });
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.job).not.toBeNull());

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });

    // job_id와 같은 계약 비대칭 — pair에 섞이면 상세 pair의 타입 계약이 오염된다.
    expect(result.current.job!.pairs[0]).not.toHaveProperty(
      "job_curation_reviewed",
    );
    expect(result.current.job!.pairs[0]).not.toHaveProperty("job_id");
  });

  it("PATCH 실패는 게이트를 건드리지 않는다", async () => {
    mockGetJob.mockResolvedValue({
      data: {
        ...jobDetail(),
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      },
    });
    mockPatchPair.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.job).not.toBeNull());

    await act(async () => {
      await result.current.patchPair(9001, { canonical_label: "배추" });
    });

    // 요청이 실패하면 서버는 게이트를 건드리지 않았다 — 화면도 따라가면 안 된다.
    expect(result.current.job!.curation_reviewed).toBe(true);
    expect(mockToastError).toHaveBeenCalled();
  });

  it("stale 성공 뒤 최신 요청이 실패해도 잡 게이트는 해제 상태로 남는다", async () => {
    // 서버는 A의 PATCH로 이미 curation_reviewed=0을 썼다. B가 실패해도 그 사실은
    // 되돌아가지 않는다 — 화면이 true로 남으면 배너가 안 뜨고 "검수 완료"가 잠긴 채
    // 서버와 발산한다(AC 2·4). 게이트 반영이 stale 가드 뒤로 내려가면 이 케이스가 깨진다.
    mockGetJob.mockResolvedValue({
      data: {
        ...jobDetail(),
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      },
    });
    const dA = deferred<{ data: CurationPairPatchResult }>();
    const dB = deferred<{ data: CurationPairPatchResult }>();
    mockPatchPair
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);

    const { result } = renderHook(() => useCurationJob(128));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let pA!: Promise<void>;
    let pB!: Promise<void>;
    await act(async () => {
      pA = result.current.patchPair(9001, { canonical_label: "A" });
    });
    await act(async () => {
      pB = result.current.patchPair(9001, { canonical_label: "B" });
    });

    await act(async () => {
      dA.resolve({ data: patchResult({ canonical_label: "A" }) }); // stale 성공 — 서버는 해제됨
      dB.reject(new Error("network")); // 최신 실패 — pair만 롤백된다
      await pA;
      await pB;
    });

    expect(result.current.job!.curation_reviewed).toBe(false);
    // 첫 검수 시각은 서버가 지우지 않는다 → 화면은 "재검수 필요"로 판별된다.
    expect(result.current.job!.curation_reviewed_at).toBe(
      "2026-06-30T08:30:00",
    );
  });
});
