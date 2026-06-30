import { useCallback, useEffect, useState } from "react";
import type { Invoice, InvoiceFilters } from "@/types/invoice";
import type { ListResponse } from "@/types/api";
import { invoiceAPI } from "@/services/api";

interface UseInvoicesReturn {
  data: Invoice[];
  total: number;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useInvoices(filters?: InvoiceFilters): UseInvoicesReturn {
  const [data, setData] = useState<Invoice[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const filtersKey = JSON.stringify(filters);

  const fetch = useCallback(async () => {
    if (filters?.limit === 0) {
      setData([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res: ListResponse<Invoice> = await invoiceAPI.getList(filters);
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "거래명세서 목록을 불러올 수 없습니다",
      );
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return { data, total, loading, error, refetch: fetch };
}

interface UseInvoiceReturn {
  data: Invoice | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useInvoice(id: number | undefined): UseInvoiceReturn {
  const [data, setData] = useState<Invoice | null>(null);
  const [loading, setLoading] = useState(!!id);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await invoiceAPI.getById(id);
      setData(res.data);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "거래명세서를 불러올 수 없습니다",
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return { data, loading, error, refetch: fetch };
}
