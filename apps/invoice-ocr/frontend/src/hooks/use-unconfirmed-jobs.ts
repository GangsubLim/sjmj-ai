import { useCallback, useEffect, useRef, useState } from "react";
import type { UnconfirmedJobSummary } from "@/types/observation";
import { ocrAPI } from "@/services/api";
import { usePageParam } from "@/hooks/use-page-param";

interface UseUnconfirmedJobsReturn {
  data: UnconfirmedJobSummary[];
  total: number;
  page: number;
  totalPages: number;
  loading: boolean;
  error: string | null;
  setPage: (p: number) => void;
  refetch: () => void;
}

export function useUnconfirmedJobs(limit = 20): UseUnconfirmedJobsReturn {
  const [data, setData] = useState<UnconfirmedJobSummary[]>([]);
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
      const res = await ocrAPI.getUnconfirmedJobs({ page, limit });
      if (myId !== reqId.current) return;
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
      setTotalPages(res.pagination?.totalPages ?? 0);
    } catch (e) {
      if (myId !== reqId.current) return;
      setError(
        e instanceof Error ? e.message : "확정 전 잡을 불러올 수 없습니다",
      );
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [page, limit]);

  useEffect(() => {
    fetch();
    return () => {
      // cleanup은 '가장 최근' 발행된 요청까지 무효화해야 한다(refetch로 시작된 in-flight 포함).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetch]);

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
