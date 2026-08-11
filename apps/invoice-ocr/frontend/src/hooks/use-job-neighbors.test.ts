import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  useJobNeighbors,
  fetchCurationPage,
  fetchUnconfirmedPage,
  type FetchPage,
} from "./use-job-neighbors";
import { curationAPI, ocrAPI } from "@/services/api";
import { CURATION_PAGE_SIZE } from "@/lib/pagination";

vi.mock("@/services/api", () => ({
  curationAPI: { getJobs: vi.fn() },
  ocrAPI: { getUnconfirmedJobs: vi.fn() },
}));

function makeFetchPage(pages: Record<number, number[]>, totalPages: number) {
  const calls: number[] = [];
  const fetchPage: FetchPage = async (p) => {
    calls.push(p);
    return { ids: pages[p] ?? [], totalPages };
  };
  return { fetchPage, calls };
}

/** 응답을 즉시 resolve하지 않고 나중에 임의 순서로 resolve할 수 있는 fetchPage.
 * 요청 역순 도착(늦게 시작한 요청이 먼저 끝나고, 먼저 시작한 요청이 나중에 끝나는 경우)을
 * 재현하기 위한 헬퍼 — owned() 소유권 가드가 옛 요청의 결과를 버리는지 검증한다. */
function makeDeferredFetchPage() {
  const calls: number[] = [];
  const deferreds: Array<{
    resolve: (v: { ids: number[]; totalPages: number }) => void;
  }> = [];
  const fetchPage: FetchPage = (p) => {
    calls.push(p);
    return new Promise((resolve) => {
      deferreds.push({ resolve });
    });
  };
  return { fetchPage, calls, deferreds };
}

function renderNeighbors(
  fetchPage: FetchPage,
  jobId: number | undefined,
  page: number,
) {
  return renderHook(
    (props: { jobId: number | undefined; page: number }) =>
      useJobNeighbors({ ...props, fetchPage }),
    { initialProps: { jobId, page } },
  );
}

describe("useJobNeighbors", () => {
  beforeEach(() => vi.clearAllMocks());

  it("페이지 가운데 항목이면 추가 조회 없이 앞뒤를 준다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2, 3] }, 1);
    const { result } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 1, page: 1 }),
    );
    expect(result.current.next).toEqual({ jobId: 3, page: 1 });
    expect(calls).toEqual([1]);
  });

  it("첫 항목이고 firstPage>1이면 이전 페이지를 앞에 이어붙여 이전을 준다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2], 2: [3, 4] }, 2);
    const { result } = renderNeighbors(fetchPage, 3, 2);

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 2, page: 1 }),
    );
    expect(result.current.next).toEqual({ jobId: 4, page: 2 });
    expect(calls).toEqual([2, 1]);
  });

  it("마지막 항목이고 lastPage<totalPages면 다음 페이지를 뒤에 이어붙여 다음을 준다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2], 2: [3, 4] }, 2);
    const { result } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 3, page: 2 }),
    );
    expect(result.current.prev).toEqual({ jobId: 1, page: 1 });
    expect(calls).toEqual([1, 2]);
  });

  it("첫 페이지의 첫 항목이면 이전이 없다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result } = renderNeighbors(fetchPage, 1, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 2, page: 1 }),
    );
    expect(result.current.prev).toBeNull();
    expect(calls).toEqual([1]);
  });

  it("마지막 페이지의 마지막 항목이면 다음이 없다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2], 2: [3, 4] }, 2);
    const { result } = renderNeighbors(fetchPage, 4, 2);

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 3, page: 2 }),
    );
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([2]);
  });

  it("jobId만 바뀌는 이동은 추가 조회를 만들지 않는다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2, 3] }, 1);
    const { result, rerender } = renderNeighbors(fetchPage, 1, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 2, page: 1 }),
    );
    rerender({ jobId: 2, page: 1 });

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 1, page: 1 }),
    );
    expect(result.current.next).toEqual({ jobId: 3, page: 1 });
    expect(calls).toEqual([1]);
  });

  it("동일 jobId/page 재렌더가 추가 호출을 만들지 않는다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2, 3] }, 1);
    const { result, rerender } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 1, page: 1 }),
    );
    rerender({ jobId: 2, page: 1 });
    rerender({ jobId: 2, page: 1 });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(calls).toEqual([1]);
  });

  it("서버 목록이 재정렬돼도 스냅샷으로 탐색이 끊기지 않는다", async () => {
    // 1페이지 마지막 잡(20)을 검수하면 서버 정렬(curation_reviewed ASC)이 바뀌어
    // 20이 뒤로 밀리고 21이 1페이지로 당겨진다. "2페이지를 다시 조회한다"는 방식이면
    // 21을 2페이지에서 못 찾아 탐색이 끊긴다 — 스냅샷은 그 드리프트를 흡수한다.
    const first = Array.from({ length: 20 }, (_, i) => i + 1); // 1..20
    const second = Array.from({ length: 20 }, (_, i) => i + 21); // 21..40
    const { fetchPage, calls } = makeFetchPage({ 1: first, 2: second }, 2);

    const { result, rerender } = renderNeighbors(fetchPage, 20, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 21, page: 2 }),
    );
    expect(calls).toEqual([1, 2]);

    // 다음 잡으로 이동 — URL의 page도 스냅샷이 기억한 2로 바뀐다.
    rerender({ jobId: 21, page: 2 });

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 20, page: 1 }),
    );
    expect(result.current.next).toEqual({ jobId: 22, page: 2 });
    expect(calls).toEqual([1, 2]); // 재조회 0회
  });

  it("확장 조회 결과에 이미 스냅샷에 있는 id가 섞여 오면 중복을 제외한다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2], 2: [2, 3] }, 2);
    const { result } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 3, page: 2 }),
    );
    expect(result.current.prev).toEqual({ jobId: 1, page: 1 });
    expect(calls).toEqual([1, 2]);
  });

  it("첫 진입에서 그 page에 없는 jobId면 재조회 없이 앞뒤 모두 없다", async () => {
    // 방금 만든 스냅샷을 같은 run에서 다시 조회할 이유가 없다 — 재초기화는 낡은
    // 스냅샷을 겨냥한 것이다(spec §4.2-3). 북마크 직접 진입에서 요청이 2배가 되던 경로.
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result } = renderNeighbors(fetchPage, 99, 1);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([1]);
  });

  it("기존 스냅샷에 없는 jobId면 재초기화하고, 그래도 없으면 앞뒤 모두 없다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result, rerender } = renderNeighbors(fetchPage, 1, 1);

    await waitFor(() =>
      expect(result.current.next).toEqual({ jobId: 2, page: 1 }),
    );
    expect(calls).toEqual([1]);

    rerender({ jobId: 99, page: 1 });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([1, 1]);
  });

  it("jobId가 undefined면 조회하지 않는다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result } = renderNeighbors(fetchPage, undefined, 1);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([]);
  });

  it("jobId가 NaN이면 조회하지 않는다", async () => {
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result } = renderNeighbors(fetchPage, Number.NaN, 1);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([]);
  });

  it.each([
    ["소수", 1.5],
    ["음수", -3],
    ["Infinity", Number.POSITIVE_INFINITY],
  ])("jobId가 %s면 조회하지 않는다", async (_label, jobId) => {
    // URL에서 온 비정수는 NaN이 아니라 통과하지만 스냅샷에서 절대 매칭되지 않는다 —
    // 무의미한 조회를 부르고, 스냅샷이 이미 있으면 "못 찾음 → 재초기화"로 누적된
    // 목록 순서를 단일 페이지로 덮어 버린다.
    const { fetchPage, calls } = makeFetchPage({ 1: [1, 2] }, 1);
    const { result } = renderNeighbors(fetchPage, jobId, 1);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([]);
  });

  it("유효 jobId에서 undefined로 바뀌면 옛 이웃이 남지 않는다", async () => {
    // 반환값을 렌더 시 파생하므로 effect의 setState 없이도 즉시 접힌다
    // (react-hooks/set-state-in-effect 회피 + 진행 중 요청은 reqId로 무효화).
    const { fetchPage } = makeFetchPage({ 1: [1, 2, 3] }, 1);
    const { result, rerender } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 1, page: 1 }),
    );

    rerender({ jobId: undefined, page: 1 });

    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("역순 도착한 옛 요청이 최신 스냅샷을 덮지 않는다", async () => {
    // 먼저 시작한 요청(page 1)이 나중에 끝나고, 나중에 시작한 요청(page 2)이 먼저
    // 끝나는 역전 상황을 재현한다. owned() 가드가 없으면 옛 요청의 스냅샷이
    // snapshotRef를 덮어써 화면 상태뿐 아니라 이어지는 탐색 순서까지 오염된다.
    const { fetchPage, calls, deferreds } = makeDeferredFetchPage();
    const { result, rerender } = renderNeighbors(fetchPage, 2, 1);

    await waitFor(() => expect(calls).toEqual([1]));

    // page 변경으로 두 번째 요청을 띄운다 — 첫 요청은 아직 pending.
    rerender({ jobId: 2, page: 2 });
    await waitFor(() => expect(calls).toEqual([1, 2]));

    // 두 번째(늦게 시작한) 요청을 먼저 resolve한다.
    deferreds[1].resolve({ ids: [21, 2, 23], totalPages: 1 });
    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 21, page: 2 }),
    );
    expect(result.current.next).toEqual({ jobId: 23, page: 2 });

    // 첫 번째(먼저 시작한) 요청을 뒤늦게 resolve한다 — 다른 이웃 데이터를 들고 있다.
    deferreds[0].resolve({ ids: [1, 2, 3], totalPages: 1 });

    // (a) 화면 상태는 여전히 두 번째 결과를 유지해야 한다 — 옛 요청에 덮이지 않는다.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(result.current.prev).toEqual({ jobId: 21, page: 2 });
    expect(result.current.next).toEqual({ jobId: 23, page: 2 });

    // (b) 이어지는 탐색도 두 번째 스냅샷을 따른다 — snapshotRef가 옛 요청으로
    // 오염됐다면 23의 이웃을 못 찾거나(추가 조회 발생) 잘못된 이웃을 준다.
    rerender({ jobId: 23, page: 2 });
    await waitFor(() =>
      expect(result.current.prev).toEqual({ jobId: 2, page: 2 }),
    );
    expect(result.current.next).toBeNull();
    expect(calls).toEqual([1, 2]); // 추가 네트워크 호출 없음 — 오염되지 않았다는 증거
  });

  it("조회가 실패하면 앞뒤 모두 없고 로딩이 끝난다", async () => {
    const fetchPage: FetchPage = () => Promise.reject(new Error("조회 실패"));
    const { result } = renderNeighbors(fetchPage, 1, 1);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.prev).toBeNull();
    expect(result.current.next).toBeNull();
  });
});

describe("fetchPage 어댑터", () => {
  beforeEach(() => vi.clearAllMocks());

  it("fetchCurationPage는 job_id 목록과 totalPages를 준다", async () => {
    vi.mocked(curationAPI.getJobs).mockResolvedValue({
      success: true,
      data: [{ job_id: 7 }, { job_id: 8 }] as never,
      pagination: { page: 2, limit: 20, total: 30, totalPages: 2 },
    });

    const res = await fetchCurationPage(2);

    expect(curationAPI.getJobs).toHaveBeenCalledWith({
      page: 2,
      limit: CURATION_PAGE_SIZE,
    });
    expect(res).toEqual({ ids: [7, 8], totalPages: 2 });
  });

  it("fetchUnconfirmedPage는 pagination이 없으면 totalPages를 1로 본다", async () => {
    vi.mocked(ocrAPI.getUnconfirmedJobs).mockResolvedValue({
      success: true,
      data: [{ job_id: 11 }] as never,
    });

    const res = await fetchUnconfirmedPage(1);

    expect(ocrAPI.getUnconfirmedJobs).toHaveBeenCalledWith({
      page: 1,
      limit: CURATION_PAGE_SIZE,
    });
    expect(res).toEqual({ ids: [11], totalPages: 1 });
  });
});
