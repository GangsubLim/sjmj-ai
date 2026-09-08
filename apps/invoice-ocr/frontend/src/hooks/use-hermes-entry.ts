import { useCallback, useEffect, useRef, useState } from "react";

import type { HermesEntryDetail } from "@/types/hermes";
import { hermesAPI } from "@/services/api";

interface UseHermesEntryReturn {
  entry: HermesEntryDetail | null;
  loading: boolean;
  error: string | null;
}

/** id 없음의 고정 반환값. 모듈 상수라 소비자 쪽 identity도 안정적이다
 * (use-job-neighbors.ts의 IDLE과 동일 관례). */
const IDLE: UseHermesEntryReturn = { entry: null, loading: false, error: null };

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
    // 무효 id 분기에서 setState하지 않는다 — react-hooks/set-state-in-effect가
    // error이고(eslint.config.js:26의 recommended, use-job-neighbors.ts와 동일 근거),
    // 반환값을 렌더 시 파생하면(아래) 상태를 건드릴 필요 자체가 없다. 진행 중이던
    // 요청은 reqId 증가로 무효화한다 — 그러지 않으면 in-flight 응답의 finally가
    // 뒤늦게 setLoading(false)를 걸러(stale 취급) loading이 내부 상태로는 고착된다
    // (겉보기 loading은 아래 파생이 가리지만, 다음에 같은 id가 다시 오면 드러난다).
    if (!id) {
      reqId.current++;
      return;
    }
    fetchEntry();
    return () => {
      // 언마운트·id 교체 후 도착하는 in-flight 응답을 stale 처리한다
      // (use-hermes-entries.ts와 같은 idiom).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [id, fetchEntry]);

  // 무효 id는 상태를 건드리지 않고 렌더 시 파생으로 접는다 — 유효→undefined 전환에서
  // 옛 entry·error가 남거나 loading이 고착되는 문제를 함께 닫는다
  // (use-job-neighbors.ts와 동일 관례).
  return id ? { entry, loading, error } : IDLE;
}
