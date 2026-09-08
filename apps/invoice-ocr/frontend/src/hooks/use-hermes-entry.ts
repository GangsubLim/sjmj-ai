import { useCallback, useEffect, useRef, useState } from "react";

import type { HermesEntryDetail } from "@/types/hermes";
import { hermesAPI } from "@/services/api";

interface UseHermesEntryReturn {
  entry: HermesEntryDetail | null;
  loading: boolean;
  error: string | null;
}

/** 대조 상세 조회(읽기 전용) — 쓰기가 없어 use-curation-job의 옵티미스틱·토큰 장치가 전부 불필요하다. */
export function useHermesEntry(id: number | undefined): UseHermesEntryReturn {
  const [entry, setEntry] = useState<HermesEntryDetail | null>(null);
  const [loading, setLoading] = useState(!!id);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);

  // setState 호출을 effect 본문 바깥의 useCallback으로 옮긴다(형제 훅 use-hermes-entries.ts와
  // 동일 idiom) — effect 본문에서 직접 setState를 부르면 react-hooks/set-state-in-effect가
  // cascading render 위험으로 막는다.
  const fetchEntry = useCallback(async () => {
    if (!id) return;
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await hermesAPI.getEntry(id);
      if (myId !== reqId.current) return;
      setEntry(res.data);
    } catch (e) {
      if (myId !== reqId.current) return;
      setError(e instanceof Error ? e.message : "건을 불러올 수 없습니다");
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchEntry();
    return () => {
      // 언마운트·id 교체 후 도착하는 in-flight 응답을 stale 처리한다
      // (use-hermes-entries.ts와 같은 idiom).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetchEntry]);

  return { entry, loading, error };
}
