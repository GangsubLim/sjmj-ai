import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  useJobNeighbors,
  fetchCurationPage,
  fetchUnconfirmedPage,
  type FetchPage,
} from "./use-job-neighbors";
import { curationAPI, ocrAPI } from "@/services/api";

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

    expect(curationAPI.getJobs).toHaveBeenCalledWith({ page: 2, limit: 20 });
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
      limit: 20,
    });
    expect(res).toEqual({ ids: [11], totalPages: 1 });
  });
});
