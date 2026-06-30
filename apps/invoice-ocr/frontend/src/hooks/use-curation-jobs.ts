import { useCallback, useEffect, useState } from "react";
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

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJobs({ page, limit });
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
      setTotalPages(res.pagination?.totalPages ?? 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "검수 큐를 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, [page, limit]);

  useEffect(() => {
    fetch();
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
