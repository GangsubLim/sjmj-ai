import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** 목록 페이지 번호 상한. 백엔드 라우터의 _PAGE_MAX와 같은 값이어야 한다
 * (거대 offset → MySQL 1064). 이 값이 어긋나면 프론트가 백엔드가 거부하는 page를 만든다. */
export const PAGE_MAX = 1_000_000_000;

/** 문자열 **전체**가 10진 정수일 때만 숫자로 본다 — Number.parseInt는 "3abc"·"3.5"를 3으로,
 * "1e2"를 1로 통과시켜 "정수가 아니면 1" 계약을 지키지 못한다.
 * 형식 오류는 1로, 범위 초과는 clamp로 가른다(후자가 백엔드의 무음 clamp 의미론과 같다). */
function parsePage(raw: string | null): number {
  if (raw === null || !/^\d+$/.test(raw)) return 1;
  return Math.min(Math.max(Number(raw), 1), PAGE_MAX);
}

export interface UsePageParamReturn {
  page: number;
  setPage: (p: number) => void;
}

/** 목록 위치(page)를 URL이 소유하게 한다 — 뒤로가기·새로고침·북마크가 같은 페이지를 복원한다. */
export function usePageParam(): UsePageParamReturn {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parsePage(searchParams.get("page"));

  const setPage = useCallback(
    (p: number) => {
      setSearchParams((prev) => {
        // 기존 파라미터를 복사한 뒤 page만 교체 — 다른 쿼리 키를 잃지 않는다.
        const nextParams = new URLSearchParams(prev);
        if (p > 1) nextParams.set("page", String(p));
        else nextParams.delete("page");
        return nextParams;
      });
    },
    [setSearchParams],
  );

  return { page, setPage };
}
