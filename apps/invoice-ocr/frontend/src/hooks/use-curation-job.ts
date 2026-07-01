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

  const fetch = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await curationAPI.getJob(jobId);
      setJob(res.data);
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
        // 2) 성공: 응답을 merge. job_id는 버리고 top5는 기존 값 보존(계약 비대칭).
        const { job_id: _jobId, ...base } = res.data;
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
        // 3) 실패: 해당 pair만 직전 값으로 롤백(동시 발행된 다른 pair 변경은 보존) + 에러 토스트.
        setJob((prev) =>
          prev
            ? {
                ...prev,
                pairs: prev.pairs.map((p) => (p.id === id ? prevPair : p)),
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
