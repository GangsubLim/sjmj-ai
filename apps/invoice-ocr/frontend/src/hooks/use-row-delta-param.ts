import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ROW_DELTA_ON,
  ROW_DELTA_PARAM,
  parseRowDelta,
} from "@/lib/curation-url";

export interface UseRowDeltaParamReturn {
  rowDelta: boolean;
  setRowDelta: (on: boolean) => void;
}

/** 행 증감 필터를 URL이 소유하게 한다 — 새로고침·뒤로가기·북마크가 같은 작업 큐를 복원한다. */
export function useRowDeltaParam(): UseRowDeltaParamReturn {
  const [searchParams, setSearchParams] = useSearchParams();
  const rowDelta = parseRowDelta(searchParams.get(ROW_DELTA_PARAM));

  const setRowDelta = useCallback(
    (on: boolean) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (on) next.set(ROW_DELTA_PARAM, ROW_DELTA_ON);
        else next.delete(ROW_DELTA_PARAM);
        // 필터를 켜고 끄면 결과 집합의 크기가 바뀐다 — 옛 page를 들고 가면 범위 밖의
        // 빈 목록이 뜨므로 1페이지로 되돌린다(page 키를 지우는 것이 곧 1페이지다).
        next.delete("page");
        return next;
      });
    },
    [setSearchParams],
  );

  return { rowDelta, setRowDelta };
}
