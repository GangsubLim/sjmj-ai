import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type {
  HermesEntrySummary,
  HermesStatus,
  HermesSummary,
} from "@/types/hermes";
import { hermesAPI } from "@/services/api";
import { usePageParam } from "@/hooks/use-page-param";
import {
  HERMES_STATUS_PARAM,
  hermesErrorMessage,
  parseHermesStatus,
} from "@/utils/hermes";

/** 목록 페이지 크기. 훅과 페이지가 반드시 같은 값을 써야 pagination 표시가 어긋나지 않는다. */
export const HERMES_PAGE_SIZE = 20;

interface UseHermesSummaryReturn {
  summary: HermesSummary | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/** 요약은 목록의 page·필터와 무관하다 — 마운트 시 한 번만 부르고, 실패했을 때만 사용자가
 * 다시 부른다. 그래서 필터를 켜도 이 수치는 "전체 기준"으로 남으며, 화면이 그 스코프를
 * 라벨로 밝혀야 한다(목록 총계와 나란히 놓이면 두 진실로 읽힌다). */
export function useHermesSummary(): UseHermesSummaryReturn {
  const [summary, setSummary] = useState<HermesSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);

  const fetchSummary = useCallback(async () => {
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await hermesAPI.getSummary();
      if (myId === reqId.current) setSummary(res.data);
    } catch (e) {
      // 요약이 죽어도 목록은 살아 있어야 한다 — 페이지가 통째로 에러로 갈아엎히지 않는다.
      if (myId === reqId.current)
        setError(hermesErrorMessage(e, "요약을 불러올 수 없습니다"));
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSummary();
    return () => {
      // 언마운트 후 도착하는 in-flight 응답을 stale 처리한다(use-hermes-entries와 같은 idiom).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetchSummary]);

  return { summary, loading, error, refetch: fetchSummary };
}

interface UseHermesEntriesReturn {
  data: HermesEntrySummary[];
  total: number;
  page: number;
  totalPages: number;
  loading: boolean;
  error: string | null;
  setPage: (p: number) => void;
  /** 상태 필터(URL 소유). 상세 왕복 후에도 유지된다. */
  status: HermesStatus | null;
  setStatus: (s: HermesStatus | null) => void;
  /** 실패한 목록을 사용자가 다시 부를 수 있게 한다 — 에러 상태가 막다른 길이 되지 않는다. */
  refetch: () => void;
}

export function useHermesEntries(
  limit = HERMES_PAGE_SIZE,
): UseHermesEntriesReturn {
  const [data, setData] = useState<HermesEntrySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  // page·status는 URL이 소유한다 — 뒤로가기·새로고침·북마크가 같은 화면을 복원한다.
  const { page, setPage } = usePageParam();
  const [searchParams, setSearchParams] = useSearchParams();
  const status = parseHermesStatus(searchParams.get(HERMES_STATUS_PARAM));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);

  const setStatus = useCallback(
    (next: HermesStatus | null) => {
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        if (next) params.set(HERMES_STATUS_PARAM, next);
        else params.delete(HERMES_STATUS_PARAM);
        // 필터를 바꾸면 결과 집합 크기가 바뀐다 — 옛 page를 들고 가면 범위 밖의 빈 목록이
        // 뜨므로 1페이지로 되돌린다(page 키를 지우는 것이 곧 1페이지다).
        params.delete("page");
        return params;
      });
    },
    [setSearchParams],
  );

  const fetch = useCallback(async () => {
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await hermesAPI.getEntries({
        page,
        limit,
        // 꺼져 있으면 키 자체를 넘기지 않는다 — 서버가 미지 status를 400으로 닫는다.
        ...(status ? { status } : {}),
      });
      if (myId !== reqId.current) return;
      setData(Array.isArray(res.data) ? res.data : []);
      setTotal(res.pagination?.total ?? 0);
      setTotalPages(res.pagination?.totalPages ?? 0);
    } catch (e) {
      if (myId !== reqId.current) return;
      setError(hermesErrorMessage(e, "목록을 불러올 수 없습니다"));
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [page, limit, status]);

  useEffect(() => {
    fetch();
    return () => {
      // 언마운트·의존성 교체 후 도착하는 in-flight 응답을 stale 처리한다
      // (use-curation-jobs와 같은 idiom).
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
    status,
    setStatus,
    refetch: fetch,
  };
}
