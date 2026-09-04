import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";

import type { StageGeometry } from "@/types/curation";
import { curationAPI } from "@/services/api";

/**
 * 기하 조회의 결과 상태.
 *
 * 판별 유니온으로 두는 것이 계약이다 — 404(관측 없음) · 409(이전 세대) · 500(손상)은
 * 화면이 서로 다르게 닫아야 하는 사실이고, 하나의 error 문자열로 뭉개면 손상이 조용한
 * 폴백으로 위장된다(spec §5-4).
 */
export type JobGeometryState =
  | { status: "loading" }
  | { status: "ready"; geometry: StageGeometry }
  | { status: "absent" }
  | { status: "stale" }
  | { status: "corrupt" }
  | { status: "error" };

function stateFromError(e: unknown): JobGeometryState {
  const code = axios.isAxiosError(e) ? e.response?.status : undefined;
  if (code === 404) return { status: "absent" };
  if (code === 409) return { status: "stale" };
  if (code === 500) return { status: "corrupt" };
  // mock 모드만 axios를 타지 않고 평범한 Error를 던진다(mocks/api.ts의 실패 관용구).
  // 그 판별을 좁히지 않으면 .then 콜백 안에서 난 임의 예외까지 "관측 없음"으로 접혀
  // spec §5-4가 금지한 조용한 폴백이 다시 들어온다.
  if (!axios.isAxiosError(e)) {
    return import.meta.env.VITE_USE_MOCK === "true"
      ? { status: "absent" }
      : { status: "error" };
  }
  return { status: "error" };
}

/** 잡의 단계 기하를 조회한다 — 상태별로 화면이 다르게 닫히도록 판별 유니온을 돌려준다. */
export function useJobGeometry(jobId: number | undefined): JobGeometryState {
  const [state, setState] = useState<JobGeometryState>({ status: "loading" });
  // "지금 이 훅이 보고 있는 잡"의 요청 일련번호. 라우트에 key가 없어 "다음 잡" 이동이
  // 컴포넌트를 재사용하므로, 늦게 도착한 옛 응답이 새 잡의 상태를 덮을 창이 실재한다.
  const reqId = useRef(0);

  // setState 호출을 effect 본문 바깥의 useCallback으로 옮긴다(형제 훅 use-curation-jobs.ts와
  // 동일 idiom) — effect 본문에서 직접 setState를 부르면 react-hooks/set-state-in-effect가
  // cascading render 위험으로 막는다.
  const fetchGeometry = useCallback(async () => {
    if (jobId === undefined) return;
    const myId = ++reqId.current;
    setState({ status: "loading" });
    try {
      const res = await curationAPI.getGeometry(jobId);
      if (myId !== reqId.current) return;
      setState({ status: "ready", geometry: res.data });
    } catch (e) {
      if (myId !== reqId.current) return;
      setState(stateFromError(e));
    }
  }, [jobId]);

  useEffect(() => {
    fetchGeometry();
    return () => {
      // cleanup은 스냅샷이 아니라 '가장 최근' 발행된 요청까지 무효화해야 한다
      // (use-curation-jobs.ts와 동일 이유) → 최신 reqId.current를 그대로 증가시킨다.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetchGeometry]);

  return state;
}
