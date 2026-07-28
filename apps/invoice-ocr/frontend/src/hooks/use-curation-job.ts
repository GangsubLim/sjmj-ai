import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type {
  CurationJobDetail,
  CurationJobPair,
  CurationPairPatch,
} from "@/types/curation";
import { curationAPI } from "@/services/api";

interface UseCurationJobReturn {
  job: CurationJobDetail | null;
  loading: boolean;
  error: string | null;
  patchPair: (id: number, patch: CurationPairPatch) => Promise<void>;
  reviewJob: () => Promise<boolean>;
  refetch: () => void;
}

// 에러를 삼키지 않고 식별 가능한 메시지로 surface한다(Error면 그 메시지, 아니면 fallback).
function errorMessage(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback;
}

export function useCurationJob(
  jobId: number | undefined,
): UseCurationJobReturn {
  const [job, setJob] = useState<CurationJobDetail | null>(null);
  const [loading, setLoading] = useState(!!jobId);
  const [error, setError] = useState<string | null>(null);

  // 옵티미스틱 롤백 스냅샷을 await 시점과 무관하게 동기로 읽기 위한 미러.
  // (setJob 함수형 업데이터의 부수효과는 React가 실행 시점을 보장하지 않아 신뢰 불가.)
  const jobRef = useRef<CurationJobDetail | null>(null);
  useEffect(() => {
    jobRef.current = job;
  }, [job]);

  // pair별 요청 시퀀스. 늦게 도착한 응답이 최신 선택을 덮거나 롤백하지 못하게 막는다.
  // 지금까지 이 레이스가 안 보인 이유는 commit이 텍스트 blur로만 나서 사람 손 속도가
  // 사실상 직렬화 역할을 했기 때문이다 — 후보 칩은 그 방어를 없앤다.
  // pending 동안 칩을 비활성화하는 대안은 쓰지 않는다(연속 교정 속도가 이 기능의 목적).
  const seqRef = useRef<Map<number, number>>(new Map());

  // pair별 '서버가 마지막으로 확인한 값'. 롤백 기준선은 요청 시작 시점의 로컬 값이 아니라
  // 이것이어야 한다 — 같은 pair에 겹친 두 요청이 모두 실패하면(네트워크 단절 시 전형적)
  // 앞 요청의 실패는 stale로 버려지고 뒤 요청은 서버에 저장된 적 없는 옵티미스틱 값으로
  // 되돌아가, 라벨링 도구가 저장되지 않은 값을 '현재 라벨'로 보여주게 된다.
  const confirmedRef = useRef<Map<number, CurationJobPair>>(new Map());

  useEffect(() => {
    // ref는 재할당되지 않는 Map이라 effect 본문에서 한 번 잡아 cleanup에서 쓴다.
    const seqs = seqRef.current;
    return () => {
      // jobId 교체·언마운트 후 도착하는 in-flight 응답을 전부 stale 처리한다(형제 훅
      // use-curation-jobs.ts의 reqId 무효화와 동일 idiom). 이전 잡/페이지의 늦게 실패한
      // PATCH가 맥락 없는 에러 토스트를 띄우는 것을 막는다.
      for (const [pairId, seq] of seqs) {
        seqs.set(pairId, seq + 1);
      }
    };
  }, [jobId]);

  const fetch = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJob(jobId);
      setJob(res.data);
      confirmedRef.current = new Map(res.data.pairs.map((p) => [p.id, p]));
    } catch (e) {
      setError(errorMessage(e, "잡을 불러올 수 없습니다"));
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const patchPair = useCallback(
    async (id: number, patch: CurationPairPatch) => {
      // 0) per-pair 스냅샷: 직전 값을 ref에서 동기로 캡처(해당 pair만).
      const prevPair: CurationJobPair | undefined = jobRef.current?.pairs.find(
        (p) => p.id === id,
      );
      if (!prevPair) return;

      // pair별 시퀀스 토큰 증가 — 이 요청이 stale해지는 기준선.
      const seq = (seqRef.current.get(id) ?? 0) + 1;
      seqRef.current.set(id, seq);
      const isStale = () => seqRef.current.get(id) !== seq;

      // 1) 옵티미스틱: 로컬 pair만 즉시 불변 갱신.
      setJob((prev) =>
        prev
          ? {
              ...prev,
              pairs: prev.pairs.map((p) =>
                p.id === id ? { ...p, ...patch } : p,
              ),
            }
          : prev,
      );
      try {
        const res = await curationAPI.patchPair(id, patch);
        if (isStale()) return; // 늦게 온 성공 — 최신 선택을 덮지 않는다
        // 2) 성공: 응답을 merge. job_id는 버리고 top5는 기존 값 보존(계약 비대칭).
        const { job_id: _jobId, ...base } = res.data;
        confirmedRef.current.set(id, {
          ...(confirmedRef.current.get(id) ?? prevPair),
          ...base,
        });
        setJob((prev) =>
          prev
            ? {
                ...prev,
                pairs: prev.pairs.map((p) =>
                  p.id === id ? { ...p, ...base } : p,
                ),
              }
            : prev,
        );
      } catch (e) {
        if (isStale()) return; // 늦게 온 실패 — 이후 성공한 선택을 되돌리지 않고 토스트도 없다
        // 3) 실패: 해당 pair만 서버 확정 스냅샷으로 롤백 + 에러 토스트.
        const rollback = confirmedRef.current.get(id) ?? prevPair;
        setJob((prev) =>
          prev
            ? {
                ...prev,
                pairs: prev.pairs.map((p) => (p.id === id ? rollback : p)),
              }
            : prev,
        );
        toast.error(errorMessage(e, "저장에 실패했습니다"));
      }
    },
    [],
  );

  const reviewJob = useCallback(async (): Promise<boolean> => {
    if (!jobId) return false;
    try {
      await curationAPI.reviewJob(jobId);
      toast.success("검수가 완료되었습니다");
      return true;
    } catch (e) {
      toast.error(errorMessage(e, "검수 완료에 실패했습니다"));
      return false;
    }
  }, [jobId]);

  return { job, loading, error, patchPair, reviewJob, refetch: fetch };
}
