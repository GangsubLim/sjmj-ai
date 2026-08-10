import { useEffect, useRef, useState } from "react";

import { curationAPI, ocrAPI } from "@/services/api";
import { CURATION_PAGE_SIZE } from "@/lib/pagination";

export interface JobNeighbor {
  jobId: number;
  /** 이 잡이 속한 목록 페이지 — 이동 URL의 ?page= 로 그대로 실린다. */
  page: number;
}

export interface UseJobNeighborsReturn {
  prev: JobNeighbor | null;
  next: JobNeighbor | null;
  loading: boolean;
}

export type FetchPage = (
  page: number,
) => Promise<{ ids: number[]; totalPages: number }>;

interface Snapshot {
  entries: JobNeighbor[]; // 목록 순서 그대로, 각 항목이 온 페이지를 함께 기억
  firstPage: number;
  lastPage: number;
  totalPages: number;
}

// 어댑터는 컴포넌트 상태를 쓰지 않는 순수 함수라 모듈 스코프에 둔다 — effect deps에
// 그대로 넣어도 identity가 고정돼 조회 루프(조회→state→렌더→재조회)가 생기지 않는다.
// limit은 목록 페이지와 반드시 같은 CURATION_PAGE_SIZE여야 페이지 경계가 맞는다.
export const fetchCurationPage: FetchPage = async (page) => {
  const res = await curationAPI.getJobs({ page, limit: CURATION_PAGE_SIZE });
  return {
    ids: res.data.map((job) => job.job_id),
    totalPages: res.pagination?.totalPages ?? 1,
  };
};

export const fetchUnconfirmedPage: FetchPage = async (page) => {
  const res = await ocrAPI.getUnconfirmedJobs({
    page,
    limit: CURATION_PAGE_SIZE,
  });
  return {
    ids: res.data.map((job) => job.job_id),
    totalPages: res.pagination?.totalPages ?? 1,
  };
};

function isNavigableJobId(jobId: number | undefined): jobId is number {
  return jobId !== undefined && !Number.isNaN(jobId);
}

/** 조회하지 않는 상태의 고정 반환값. 모듈 상수라 소비자 쪽 identity도 안정적이다. */
const IDLE: UseJobNeighborsReturn = { prev: null, next: null, loading: false };

/** 상세에서 앞뒤 잡을 준다. 목록 순서를 ref 스냅샷에 누적해 들고 있어, 검수 완료로
 * 서버 정렬(curation_reviewed ASC)이 바뀌어도 이미 확보한 순서로 계속 이동한다. */
export function useJobNeighbors({
  jobId,
  page,
  fetchPage,
}: {
  jobId: number | undefined;
  page: number;
  fetchPage: FetchPage;
}): UseJobNeighborsReturn {
  const navigable = isNavigableJobId(jobId);
  // 라우트에 key가 없어 jobId만 바뀌면 element가 재사용된다(main.tsx:95) — 그래서
  // 잡 간 이동에도 이 ref는 살아남는다. 목록 경유·새로고침이면 새 스냅샷이 만들어진다.
  const snapshotRef = useRef<Snapshot | null>(null);
  const reqId = useRef(0);
  const [state, setState] = useState<UseJobNeighborsReturn>(() =>
    isNavigableJobId(jobId) ? { prev: null, next: null, loading: true } : IDLE,
  );

  useEffect(() => {
    // 무효 jobId 분기에서 setState를 하지 않는다 — react-hooks/set-state-in-effect가
    // error이고(eslint.config.js:26의 recommended), 반환값을 렌더 시 파생하면 상태를
    // 건드릴 필요 자체가 없다. 진행 중이던 요청은 여기서도 무효화한다.
    if (!navigable) {
      reqId.current += 1;
      return;
    }

    reqId.current += 1;
    const myId = reqId.current;
    const owned = () => myId === reqId.current;

    const commit = (prev: JobNeighbor | null, next: JobNeighbor | null) => {
      if (!owned()) return; // 늦게 도착한 옛 요청은 버린다
      setState({ prev, next, loading: false });
    };

    const load = async (target: number): Promise<Snapshot> => {
      const { ids, totalPages } = await fetchPage(target);
      return {
        entries: ids.map((id) => ({ jobId: id, page: target })),
        firstPage: target,
        lastPage: target,
        totalPages,
      };
    };

    const extend = async (
      snapshot: Snapshot,
      target: number,
      side: "before" | "after",
    ): Promise<Snapshot> => {
      const { ids, totalPages } = await fetchPage(target);
      const known = new Set(snapshot.entries.map((e) => e.jobId));
      // 재정렬로 이미 본 잡이 다시 나올 수 있다 — id로 걸러 중복 항목을 만들지 않는다.
      const fresh = ids
        .filter((id) => !known.has(id))
        .map((id) => ({ jobId: id, page: target }));
      return side === "before"
        ? {
            entries: [...fresh, ...snapshot.entries],
            firstPage: target,
            lastPage: snapshot.lastPage,
            totalPages,
          }
        : {
            entries: [...snapshot.entries, ...fresh],
            firstPage: snapshot.firstPage,
            lastPage: target,
            totalPages,
          };
    };

    const run = async () => {
      setState((s) => (s.loading ? s : { ...s, loading: true }));
      try {
        // 스냅샷은 요청 로컬 변수로 계산하고 마지막에 한 번만 ref에 커밋한다 —
        // 늦게 끝난 옛 요청이 최신 스냅샷을 덮으면 화면 가드(commit)를 통과하지
        // 않아도 다음 렌더의 탐색 순서가 오염된다.
        let snapshot = snapshotRef.current;
        let loadedNow = false;
        if (snapshot === null) {
          snapshot = await load(page);
          loadedNow = true;
        }
        let i = snapshot.entries.findIndex((e) => e.jobId === jobId);
        // 방금 만든 스냅샷이면 재초기화하지 않는다(같은 page를 즉시 두 번 조회하는 낭비).
        if (i === -1 && !loadedNow) {
          snapshot = await load(page);
          i = snapshot.entries.findIndex((e) => e.jobId === jobId);
        }
        // 확장 조회는 양 끝 각각 최대 1회.
        if (i !== -1 && i === 0 && snapshot.firstPage > 1) {
          snapshot = await extend(snapshot, snapshot.firstPage - 1, "before");
          i = snapshot.entries.findIndex((e) => e.jobId === jobId);
        }
        if (
          i !== -1 &&
          i === snapshot.entries.length - 1 &&
          snapshot.lastPage < snapshot.totalPages
        ) {
          snapshot = await extend(snapshot, snapshot.lastPage + 1, "after");
          i = snapshot.entries.findIndex((e) => e.jobId === jobId);
        }
        if (!owned()) return;
        snapshotRef.current = snapshot;
        commit(
          i > 0 ? snapshot.entries[i - 1] : null,
          i !== -1 && i < snapshot.entries.length - 1
            ? snapshot.entries[i + 1]
            : null,
        );
      } catch {
        // 이웃 조회 실패는 부가 기능의 실패다 — 전체 화면 에러로 승격시키지 않고
        // 이전/다음만 비활성으로 둔다(목록 버튼으로 탈출 가능).
        commit(null, null);
      }
    };

    void run();

    return () => {
      // 언마운트·jobId/page 교체 시 진행 중이던 요청을 무효화한다.
      reqId.current += 1;
    };
  }, [navigable, jobId, page, fetchPage]);

  // 무효 jobId는 상태를 건드리지 않고 렌더 시 파생으로 접는다 — 유효→undefined 전환에서
  // 옛 이웃이 남는 문제도 함께 닫힌다.
  return navigable ? state : IDLE;
}
