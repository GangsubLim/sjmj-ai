import { useCallback, useEffect, useRef, useState } from "react";
import type { CurationJobSummary } from "@/types/curation";
import { curationAPI } from "@/services/api";
import { usePageParam } from "@/hooks/use-page-param";
import { CURATION_PAGE_SIZE } from "@/lib/pagination";

interface UseCurationJobsReturn {
  data: CurationJobSummary[];
  total: number;
  page: number;
  totalPages: number;
  loading: boolean;
  error: string | null;
  setPage: (p: number) => void;
  refetch: () => void;
}

export function useCurationJobs(
  limit = CURATION_PAGE_SIZE,
): UseCurationJobsReturn {
  const [data, setData] = useState<CurationJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  // page는 URL이 소유한다 — 뒤로가기·새로고침이 보던 페이지를 복원한다(useState(1)이면 1로 리셋).
  const { page, setPage } = usePageParam();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);

  const fetch = useCallback(async () => {
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJobs({ page, limit });
      if (myId !== reqId.current) return;
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
      setTotalPages(res.pagination?.totalPages ?? 0);
    } catch (e) {
      if (myId !== reqId.current) return;
      setError(e instanceof Error ? e.message : "검수 큐를 불러올 수 없습니다");
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [page, limit]);

  useEffect(() => {
    fetch();
    return () => {
      // cleanup은 스냅샷이 아니라 '가장 최근' 발행된 요청까지 무효화해야 한다
      // (refetch로 시작된 in-flight 포함) → 최신 reqId.current를 그대로 증가시킨다.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetch]);

  // usePageParam은 [1, PAGE_MAX]로만 clamp할 뿐 목록 길이를 모른다 — 북마크·뒤로가기·상세
  // 복귀(?page=N)로 들어왔는데 그 사이 목록이 줄었으면 헤더는 "총 N건"인데 본문은 비고,
  // 호출부가 totalPages>1일 때만 Pagination을 그리므로 되돌아갈 UI마저 없다.
  // 조회가 끝난 뒤(loading=false) totalPages를 알 때만 마지막 페이지로 접는다 —
  // 조회 중 보정은 재조회를 부르는 루프가 된다.
  useEffect(() => {
    if (loading || totalPages <= 0 || page <= totalPages) return;
    setPage(totalPages);
  }, [loading, page, totalPages, setPage]);

  return {
    data,
    total,
    page,
    totalPages,
    loading,
    error,
    setPage,
    refetch: fetch,
  };
}
