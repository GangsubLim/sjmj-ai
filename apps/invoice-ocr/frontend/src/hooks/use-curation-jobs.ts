import { useCallback, useEffect, useRef, useState } from "react";
import type { CurationJobSummary } from "@/types/curation";
import { curationAPI } from "@/services/api";

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

export function useCurationJobs(limit = 20): UseCurationJobsReturn {
  const [data, setData] = useState<CurationJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [page, setPage] = useState(1);
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
