import { useCallback, useEffect, useState } from "react";
import type { Item } from "@/types/item";
import { itemSuggestionsAPI } from "@/services/api";

interface UseItemsReturn {
  data: Item[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useItems(query?: string, category?: string): UseItemsReturn {
  const [data, setData] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await itemSuggestionsAPI.getList(query, category);
      setData(res.data);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "품목 목록을 불러올 수 없습니다",
      );
    } finally {
      setLoading(false);
    }
  }, [query, category]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return { data, loading, error, refetch: fetch };
}
