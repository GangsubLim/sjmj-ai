import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  MonthlySalesData,
  SalesRecord,
  SalesRecordUpsertInput,
} from "@/types/sales-record";
import type { Salesperson } from "@/types/salesperson";
import { salesRecordAPI } from "@/services/api";

interface UseSalesRecordsReturn {
  salespeople: Salesperson[];
  records: SalesRecord[];
  recordsByDate: Map<string, Map<number, SalesRecord>>;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  upsertRecord: (input: SalesRecordUpsertInput) => Promise<void>;
  removeRecord: (id: number) => Promise<void>;
}

export function useSalesRecords(year: number, month: number): UseSalesRecordsReturn {
  const [data, setData] = useState<MonthlySalesData>({
    salespeople: [],
    records: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await salesRecordAPI.getMonthly(year, month);
      setData(res.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "실적을 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, [year, month]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const recordsByDate = useMemo(() => {
    const m = new Map<string, Map<number, SalesRecord>>();
    for (const r of data.records) {
      if (!m.has(r.work_date)) m.set(r.work_date, new Map());
      m.get(r.work_date)!.set(r.salesperson_id, r);
    }
    return m;
  }, [data.records]);

  const upsertRecord = useCallback(
    async (input: SalesRecordUpsertInput) => {
      await salesRecordAPI.upsert(input);
    },
    [],
  );

  const removeRecord = useCallback(async (id: number) => {
    await salesRecordAPI.remove(id);
  }, []);

  return {
    salespeople: data.salespeople,
    records: data.records,
    recordsByDate,
    loading,
    error,
    refetch: fetch,
    upsertRecord,
    removeRecord,
  };
}
