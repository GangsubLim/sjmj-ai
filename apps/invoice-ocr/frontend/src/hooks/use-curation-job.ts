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

  // "지금 이 훅이 보고 있는 잡"의 항상-최신 미러. useEffect가 아니라 렌더 본문에서 직접
  // 대입한다 — 늦은 응답의 소유권 검사(아래 jobTokenRef 가드)가 커밋을 기다리면, 같은
  // 컴포넌트 인스턴스가 언마운트 없이 jobId만 바꿔 재사용될 때(예: "다음 잡" 이동) 그
  // 사이에 도착한 옛 잡의 응답이 새 잡의 토큰을 덮을 창이 생긴다.
  const jobIdRef = useRef(jobId);
  jobIdRef.current = jobId;

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

  // 잡의 최신 토큰 — jobRef와 별개로 둔다. jobRef는 useEffect로 동기화돼 커밋 타이밍에
  // 좌우되지만, 아래 patchQueueRef가 체이닝하는 다음 발행은 그 커밋을 기다릴 수 없다
  // (React 렌더 커밋보다 먼저 다음 프라미스가 이어질 수 있다). PATCH 성공 시 setJob과
  // 같은 지점에서 동기로 같이 갱신한다.
  const jobTokenRef = useRef<string | undefined>(undefined);

  // 같은 잡의 PATCH "네트워크 발행"을 직렬화하는 큐(진짜 요청 자체, 옵티미스틱 반영은
  // 아님). 두 pair를 같은 이벤트 턴에 고치면(1행 blur 커밋 + 2행 칩 클릭) 서버가 첫
  // PATCH에서 이미 토큰을 튀기므로, 직렬화 없이 두 요청이 동시에 나가면 옛 토큰을 든
  // 두 번째가 확정적으로 409를 받는다. 이 큐는 그 순서만 강제한다 — 실제로 잡이 다른
  // 곳에서 바뀐 경우의 409는 여전히 그대로 난다(자동 재시도가 아니다).
  const patchQueueRef = useRef<Promise<void>>(Promise.resolve());

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
      // 옛 잡의 큐/토큰을 새 잡으로 들고 가지 않는다 — 새 잡의 첫 PATCH가 옛 잡의
      // 남은 발행(들)을 기다릴 이유가 없다(정확성은 아래 dispatch의 소유권 가드가
      // 이미 보장하지만, 리셋 없이는 새 잡의 요청이 무관한 옛 잡 요청 뒤에 줄을 선다).
      // 실제 정확성 백스톱은 jobIdRef 가드다 — 이 리셋만으로는 늦게 도착하는 옛 잡
      // 응답의 쓰기를 막지 못한다(그 응답 자체는 취소되지 않는다).
      patchQueueRef.current = Promise.resolve();
      jobTokenRef.current = undefined;
    };
  }, [jobId]);

  const fetch = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!jobId) return;
      // silent: review 성공 직후 토큰만 맞추는 재조회. loading/error를 건드리면
      // 페이지가 전체 화면 스켈레톤·에러로 갈아치워 "검수 완료" 토스트와 모순된 화면을
      // 만든다(§리뷰 Important 2) — 그래서 setJob·confirmedRef만 갱신한다.
      const silent = options?.silent ?? false;
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const res = await curationAPI.getJob(jobId);
        // dispatch와 같은 소유권 가드 — 같은 컴포넌트 인스턴스가 언마운트 없이 jobId만
        // 바꿔 재사용되면(“다음 잡” 이동) 늦게 도착한 옛 잡의 GET이 새 잡의 토큰·화면·
        // 확정 스냅샷을 통째로 덮는다. PATCH 경로만 막고 여기를 비워두면 이 diff가
        // 없애려던 오염이 GET으로 그대로 남는다.
        if (jobId !== jobIdRef.current) return;
        setJob(res.data);
        jobTokenRef.current = res.data.job_token;
        confirmedRef.current = new Map(res.data.pairs.map((p) => [p.id, p]));
      } catch (e) {
        // silent 재조회 실패는 검수 완료 자체(이미 서버에 반영됨)를 실패로 보이게
        // 하면 안 된다 — 화면 상태는 건드리지 않고 조용히 넘어간다. 다음 PATCH가 낡은
        // 토큰으로 나가면 그때는 기존 409 처리(STALE_JOB_MESSAGE)가 담당한다.
        if (!silent) setError(errorMessage(e, "잡을 불러올 수 없습니다"));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [jobId],
  );

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
      // 네트워크 발행 본체 — patchQueueRef 큐에 태워 잡 단위로 직렬화한다(아래).
      // 토큰은 컴포넌트가 아니라 훅이 채운다 — 화면에 열려 있는 잡의 세대가 유일한 진실원.
      const dispatch = async () => {
        const jobToken = jobTokenRef.current;
        try {
          const res = await curationAPI.patchPair(id, {
            ...patch,
            // 토큰이 비어 있으면 서버가 400으로 닫는다 — 키를 떨궈 원인이 흐려지는 것보다
            // 형식 오류로 드러나는 편이 낫다(reviewJob과 같은 처리).
            job_token: jobToken ?? "",
          });
          // 2) 성공: 응답을 merge. job_id·게이트·토큰은 pair에서 떼고 top5는 기존 값 보존(계약 비대칭).
          const {
            job_id: pairJobId,
            job_curation_reviewed: gate,
            job_token: nextToken,
            ...base
          } = res.data;
          // 큐의 다음 항목이 곧바로 최신 토큰을 읽어야 한다 — jobRef(useEffect 동기화)는
          // 렌더 커밋을 기다려 늦을 수 있어 여기서 동기로 먼저 갱신한다. 단, 이 응답이
          // "지금 이 훅이 보고 있는 잡"의 것일 때만 — jobId가 언마운트 없이 바뀐 뒤
          // 도착한 옛 잡의 응답이 새 잡의 토큰을 덮으면, 다음 편집이 남의 토큰으로 나가
          // 확정적 409가 된다(§리뷰 New Important). setJob의 job_id 대조(아래)와 같은
          // 가드를 여기서도 반복한다 — 두 쓰기는 서로 다른 진실원(jobTokenRef vs job
          // state)이라 각자 자기 가드가 필요하다.
          if (pairJobId === jobIdRef.current) {
            jobTokenRef.current = nextToken;
          }
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
      };

      // 옵티미스틱 반영(위)은 즉시 끝났다 — 큐는 "네트워크 발행"만 늦춘다.
      // 큐 링크는 rejection을 물려받지 않는다(catch로 잘라낸다) — 한 번이라도 새면
      // patchQueueRef가 rejected로 굳어 이후 모든 발행이 요청 없이 옵티미스틱 반영만
      // 되고 토스트도 뜨지 않는(무성 유실) 상태가 된다. dispatch가 지금은 모든 에러를
      // 삼키지만, 그 사실에 큐의 생존을 걸지 않는다.
      const queued = patchQueueRef.current.then(dispatch);
      patchQueueRef.current = queued.catch(() => undefined);
      await queued;
    },
    [],
  );

  const reviewJob = useCallback(async (): Promise<boolean> => {
    if (!jobId) return false;
    const run = async (): Promise<boolean> => {
      try {
        // 게이트를 닫는 쓰기도 세대 대조를 받는다 — 재처리로 열린 게이트를 옛 화면이
        // 다시 닫으면 새 미결 쌍이 사람 눈에 닿기 전에 검수 큐에서 사라진다(spec §7·§12).
        // jobTokenRef를 읽는다 — patchPair의 네트워크 발행이 쓰는 진실원과 같은 곳이어야
        // review와 PATCH가 겹쳤을 때 서로 다른(어긋난) 토큰을 들고 나가지 않는다.
        await curationAPI.reviewJob(jobId, jobTokenRef.current ?? "");
        // review 응답에는 갱신된 토큰이 없다(계약 갭) — 재조회로 메꾸지 않으면 검수 완료
        // 직후 같은 화면에서 라벨을 고치는 흐름(Issue #52가 게이트 해제로 만든 1급 흐름)이
        // 사용자 자신의 검수 완료 클릭 때문에 409가 된다. silent로 불러 페이지 전체를
        // 스켈레톤/에러로 갈아치우지 않는다(§리뷰 Important 2) — 그건 review POST 성공과
        // 무관한 화면 상태다.
        await fetch({ silent: true });
        // 재조회가 실패해도 POST는 성공했다 — 서버 진실에 맞춰 로컬을 접는다.
        // jobIdRef 가드는 필수다: 검수 완료 클릭 후 응답 전에 "다음 →"으로 이동하면
        // 이 setJob이 **새 잡**의 detail에 curation_reviewed=true를 칠해, 검수한 적
        // 없는 잡의 버튼이 비활성되고 배지가 오표시된다(재현 확인). fetch 내부의
        // `if (jobId !== jobIdRef.current) return;`(같은 파일)과 같은 소유권 규칙이다.
        if (jobId === jobIdRef.current) {
          setJob((prev) =>
            prev && !prev.curation_reviewed
              ? { ...prev, curation_reviewed: true }
              : prev,
          );
        }
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
    };
    // review도 PATCH와 같은 큐 뒤에 선다. 가장 흔한 흐름인 "라벨 수정 → 검수 완료
    // 클릭"에서 버튼 mousedown이 Autocomplete를 blur시켜 patchPair가 먼저 나가는데,
    // 큐에 합류하지 않으면 응답 전에 GET 시점의 옛 토큰을 실어 확정적 409가 된다
    // (반대 순서로 서버에 닿으면 PATCH가 409로 롤백돼 방금 고친 라벨이 사라진다).
    const queued = patchQueueRef.current.then(run);
    patchQueueRef.current = queued.then(
      () => undefined,
      () => undefined,
    );
    return queued;
  }, [jobId, fetch]);

  return { job, loading, error, patchPair, reviewJob, refetch: fetch };
}
