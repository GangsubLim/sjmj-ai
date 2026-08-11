import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import type { JobNeighbor } from "@/hooks/use-job-neighbors";
import { jobDetailUrl, jobListUrl } from "@/lib/curation-url";

interface JobNavButtonsProps {
  /** "/curation"(확정 후) 또는 "/curation/pending"(확정 전). 후행 슬래시 없음. */
  basePath: string;
  /** 현재 목록 페이지 — 목록 복귀 URL에 그대로 실린다. */
  page: number;
  prev: JobNeighbor | null;
  next: JobNeighbor | null;
  loading: boolean;
}

// 두 상세 페이지가 공유한다(JobImagePanel 공유와 같은 선례). 도메인 규칙이 아니라 순수
// 네비게이션이라 ADR 0009의 관문/비관문 경계를 침범하지 않는다.
export function JobNavButtons({
  basePath,
  page,
  prev,
  next,
  loading,
}: JobNavButtonsProps) {
  const navigate = useNavigate();

  // 이전/다음은 replace — 여러 번 눌러도 브라우저 뒤로가기 한 번이면 보던 목록 페이지로
  // 돌아온다. 이동 URL의 page는 스냅샷 항목이 기억하는 페이지를 쓴다.
  const goToNeighbor = (neighbor: JobNeighbor) =>
    navigate(jobDetailUrl(basePath, neighbor.jobId, neighbor.page), {
      replace: true,
    });

  return (
    // 이 nav는 로딩·에러 분기에서도 마운트된 채 남는다 — 이전/다음이 비활성인 이유가
    // "조회 중"인지 "이웃 없음"인지를 보조기술에 전달한다.
    <nav
      aria-label="잡 이동"
      aria-busy={loading}
      className="mb-3 flex items-center justify-between"
    >
      <Button
        variant="outline"
        size="sm"
        onClick={() => navigate(jobListUrl(basePath, page))}
      >
        ← 목록
      </Button>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={loading || prev === null}
          onClick={() => prev !== null && goToNeighbor(prev)}
        >
          ← 이전
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={loading || next === null}
          onClick={() => next !== null && goToNeighbor(next)}
        >
          다음 →
        </Button>
      </div>
    </nav>
  );
}
