import { useCallback, useEffect, useState } from "react";
import type { Salesperson, SalespersonInput } from "@/types/salesperson";
import { salespersonAPI } from "@/services/api";

interface UseSalespeopleReturn {
  data: Salesperson[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  create: (input: SalespersonInput) => Promise<void>;
  update: (id: number, input: Partial<Salesperson>) => Promise<void>;
  softDelete: (id: number) => Promise<void>;
}

export function useSalespeople(): UseSalespeopleReturn {
  const [data, setData] = useState<Salesperson[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await salespersonAPI.getList();
      setData(res.data);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "영업사원 목록을 불러올 수 없습니다",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const create = useCallback(
    async (input: SalespersonInput) => {
      await salespersonAPI.create(input);
      await fetch();
    },
    [fetch],
  );

  const update = useCallback(
    async (id: number, input: Partial<Salesperson>) => {
      await salespersonAPI.update(id, input);
      await fetch();
    },
    [fetch],
  );

  const softDelete = useCallback(
    async (id: number) => {
      await salespersonAPI.remove(id);
      await fetch();
    },
    [fetch],
  );

  return { data, loading, error, refetch: fetch, create, update, softDelete };
}
