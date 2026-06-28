import { useCallback, useEffect, useState } from "react";
import type { Company } from "@/types/company";
import { companySuggestionsAPI } from "@/services/api";

interface UseCompaniesReturn {
  data: Company[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useCompanies(query?: string): UseCompaniesReturn {
  const [data, setData] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await companySuggestionsAPI.getList(query);
      setData(res.data);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "거래처 목록을 불러올 수 없습니다",
      );
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return { data, loading, error, refetch: fetch };
}
