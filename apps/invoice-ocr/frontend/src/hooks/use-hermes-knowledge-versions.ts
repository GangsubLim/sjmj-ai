import { useEffect, useState } from "react";

import type { HermesKnowledgeVersion } from "@/types/hermes";
import { hermesAPI } from "@/services/api";

interface UseHermesKnowledgeVersionsReturn {
  /** 서버가 준 최신순 그대로. 정렬·재판정하지 않는다. */
  versions: HermesKnowledgeVersion[];
  loading: boolean;
  error: string | null;
}

/** 판독 지식 버전 changelog. 요약(useHermesSummary)과 훅을 분리해 이 API가 죽어도
 * 요약 카드·목록은 살아 있게 한다. 마운트 시 한 번만 부른다. */
export function useHermesKnowledgeVersions(): UseHermesKnowledgeVersionsReturn {
  const [versions, setVersions] = useState<HermesKnowledgeVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    hermesAPI
      .getKnowledgeVersions()
      .then((res) => {
        if (alive) setVersions(res.data);
      })
      .catch((e: unknown) => {
        if (alive)
          setError(
            e instanceof Error ? e.message : "지식 버전을 불러올 수 없습니다",
          );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { versions, loading, error };
}
