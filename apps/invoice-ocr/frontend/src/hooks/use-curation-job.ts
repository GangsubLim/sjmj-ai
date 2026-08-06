import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
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

// 409는 "이 잡이 다른 곳에서 바뀌었다"는 뜻이라 재시도가 아니라 새로고침이 답이다(spec §12).
const STALE_JOB_MESSAGE =
  "다른 곳에서 이 잡이 바뀌었습니다 — 새로고침한 뒤 다시 시도하세요";

function isStaleJobError(e: unknown): boolean {
  return axios.isAxiosError(e) && e.response?.status === 409;
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

  // 확정값을 기록한 요청의 seq. 늦게 온 성공도 서버에 저장된 사실이라 확정값에는 반영해야
  // 하는데, 응답 순서는 발행 순서와 다를 수 있어 뒤늦게 도착한 옛 성공이 더 최신 확정을
  // 되돌릴 수 있다 — 발행 순서가 더 뒤인 확정만 받아들여 기준선의 후퇴를 막는다.
  const confirmedSeqRef = useRef<Map<number, number>>(new Map());

  // 최신 요청이 실패해 화면을 확정값으로 되돌린 시점의 seq. 이 상태에서는 화면이 확정값을
  // 그대로 비추고 있어 덮을 선택이 없으므로, 뒤늦게 도착한 성공이 확정값을 갱신하면 화면도
  // 따라가야 한다(멈추면 서버엔 저장됐는데 화면은 옛값인 발산이 그대로 남는다).
  const rolledBackSeqRef = useRef<Map<number, number>>(new Map());

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
      // 토큰은 컴포넌트가 아니라 훅이 채운다 — 화면에 열려 있는 잡의 세대가 유일한 진실원.
      const jobToken = jobRef.current?.job_token;
      try {
        const res = await curationAPI.patchPair(id, {
          ...patch,
          job_token: jobToken,
        });
        // 2) 성공: 응답을 merge. job_id·게이트·토큰은 pair에서 떼고 top5는 기존 값 보존(계약 비대칭).
        const {
          job_id: pairJobId,
          job_curation_reviewed: gate,
          job_token: nextToken,
          ...base
        } = res.data;
        // 게이트와 같은 이유로 토큰도 잡 단위 서버 사실이라 stale 가드보다 먼저 반영한다 —
        // 늦게 온 성공을 버리면 다음 PATCH가 낡은 토큰으로 나가 409를 만든다.
        setJob((prev) =>
          prev && prev.job_id === pairJobId
            ? { ...prev, curation_reviewed: gate, job_token: nextToken }
            : prev,
        );
        // 성공은 stale이어도 '서버가 저장했다'는 사실이므로 확정값에는 먼저 반영한다 —
        // 여기서 그냥 버리면 뒤이은 최신 요청의 실패가 저장된 적 있는 값을 건너뛰고 옛
        // 값으로 롤백해, 라벨링 도구가 서버와 다른 라벨을 보여준다.
        if (seq <= (confirmedSeqRef.current.get(id) ?? 0)) return;
        confirmedSeqRef.current.set(id, seq);
        confirmedRef.current.set(id, {
          ...(confirmedRef.current.get(id) ?? prevPair),
          ...base,
        });
        // 화면은 이 요청이 최신일 때만 덮는다 — 예외는 최신 요청이 이미 실패해 화면이
        // 확정값을 비추고 있는 경우(덮을 선택이 없고, 멈추면 발산이 남는다).
        const isRolledBack =
          rolledBackSeqRef.current.get(id) === seqRef.current.get(id);
        if (isStale() && !isRolledBack) return; // 늦게 온 성공 — 최신 선택을 덮지 않는다
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
        rolledBackSeqRef.current.set(id, seq); // 이후 도착할 성공이 화면까지 갱신하도록
        setJob((prev) =>
          prev
            ? {
                ...prev,
                pairs: prev.pairs.map((p) => (p.id === id ? rollback : p)),
              }
            : prev,
        );
        toast.error(
          isStaleJobError(e)
            ? STALE_JOB_MESSAGE
            : errorMessage(e, "저장에 실패했습니다"),
        );
      }
    },
    [],
  );

  const reviewJob = useCallback(async (): Promise<boolean> => {
    if (!jobId) return false;
    try {
      // 게이트를 닫는 쓰기도 세대 대조를 받는다 — 재처리로 열린 게이트를 옛 화면이
      // 다시 닫으면 새 미결 쌍이 사람 눈에 닿기 전에 검수 큐에서 사라진다(spec §7·§12).
      await curationAPI.reviewJob(jobId, jobRef.current?.job_token ?? "");
      // review 응답에는 갱신된 토큰이 없다(계약 갭) — 재조회로 메꾸지 않으면 검수 완료
      // 직후 같은 화면에서 라벨을 고치는 흐름(Issue #52가 게이트 해제로 만든 1급 흐름)이
      // 사용자 자신의 검수 완료 클릭 때문에 409가 된다.
      await fetch();
      toast.success("검수가 완료되었습니다");
      return true;
    } catch (e) {
      toast.error(
        isStaleJobError(e)
          ? STALE_JOB_MESSAGE
          : errorMessage(e, "검수 완료에 실패했습니다"),
      );
      return false;
    }
  }, [jobId, fetch]);

  return { job, loading, error, patchPair, reviewJob, refetch: fetch };
}
