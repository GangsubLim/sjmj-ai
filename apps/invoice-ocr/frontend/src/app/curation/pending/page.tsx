import { useNavigate } from "react-router-dom";
import { InboxIcon } from "lucide-react";

import { useUnconfirmedJobs } from "@/hooks/use-unconfirmed-jobs";
import { CurationTabs } from "@/components/curation/CurationTabs";
import { PageContainer } from "@/components/layout";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { getVisiblePages } from "@/lib/pagination";
import type { ObservationStatus } from "@/types/observation";

// 배지 라벨은 관측된 사실까지만 말한다 — "워프 없음"을 "전표 미검출"이라 부르지 않는 이유는
// 파일 부재가 쿼드 미검출을 함의하지 않기 때문이다(저장 실패·사후 유실도 같은 관측).
const BADGE_LABELS: Record<ObservationStatus, string> = {
  pending: "대기",
  running: "처리중",
  failed: "실패",
  no_result: "결과 없음",
  no_warp: "워프 없음",
  demoted: "강등",
  no_rows: "행 미검출",
  unconfirmed: "미확정",
};

const BADGE_CLASSES: Record<ObservationStatus, string> = {
  pending: "text-muted-foreground",
  running: "text-blue-600",
  failed: "text-destructive",
  no_result: "text-destructive",
  no_warp: "text-amber-600",
  demoted: "text-amber-600",
  no_rows: "text-amber-600",
  unconfirmed: "text-green-600",
};

// 대기 배지의 뜻이 "오래 머물면 적체"(spec.md:37)라 날짜만으로는 당일 적체를 못 읽는다 → 분까지.
// 기존 app/curation/page.tsx의 MM-DD는 건드리지 않는다(인접 변경 0).
function formatDate(iso: string): string {
  return iso.slice(5, 16).replace("T", " "); // MM-DD HH:mm
}

export default function UnconfirmedJobsPage() {
  const navigate = useNavigate();
  const { data, total, page, totalPages, loading, error, setPage } =
    useUnconfirmedJobs(20);
  const visiblePages = getVisiblePages(page, totalPages);

  const goToJob = (jobId: number) => navigate(`/curation/pending/${jobId}`);

  return (
    <PageContainer className="py-4">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold">확정 전 잡 관측</h1>
        <span className="text-muted-foreground text-sm">총 {total}건</span>
      </div>

      <CurationTabs active="pending" />

      {error && (
        <p className="text-destructive py-8 text-center text-sm">{error}</p>
      )}

      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {!loading && !error && data.length === 0 && (
        <EmptyState
          icon={InboxIcon}
          title="확정을 기다리는 잡이 없습니다"
          description="업로드된 잡이 확정되기 전까지 여기에 표시됩니다."
        />
      )}

      {!loading && !error && data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground border-b text-left">
            <tr>
              <th className="py-2">잡</th>
              <th>관측 상태</th>
              <th>행수</th>
              <th>생성일</th>
            </tr>
          </thead>
          <tbody>
            {/* 행 전체는 마우스 편의용 onClick, 키보드·SR 진입점은 셀 내부 네이티브 button */}
            {data.map((job) => (
              <tr
                key={job.job_id}
                className="hover:bg-muted/50 cursor-pointer border-b"
                onClick={() => goToJob(job.job_id)}
              >
                <td className="py-2 font-medium">
                  <button
                    type="button"
                    aria-label={`잡 #${job.job_id} 관측 상세`}
                    className="focus-visible:ring-ring rounded font-medium hover:underline focus-visible:ring-2 focus-visible:outline-none"
                    onClick={(e) => {
                      e.stopPropagation();
                      goToJob(job.job_id);
                    }}
                  >
                    #{job.job_id}
                  </button>
                </td>
                <td>
                  <span className={BADGE_CLASSES[job.observation_status]}>
                    {BADGE_LABELS[job.observation_status]}
                  </span>
                  {job.error !== null && (
                    <span className="text-muted-foreground ml-2 text-xs">
                      {job.error}
                    </span>
                  )}
                </td>
                {/* 0행 검출과 "아직 모름"은 다른 사실이다 — null은 0이 아니라 —로 그린다. */}
                <td>{job.row_count === null ? "—" : job.row_count}</td>
                <td>{formatDate(job.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!loading && !error && totalPages > 1 && (
        <Pagination className="mt-4">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                onClick={() => setPage(Math.max(1, page - 1))}
                aria-disabled={page <= 1}
                tabIndex={page <= 1 ? -1 : undefined}
                className={page <= 1 ? "pointer-events-none opacity-50" : ""}
              />
            </PaginationItem>
            {visiblePages[0] > 1 && (
              <PaginationItem>
                <PaginationEllipsis />
              </PaginationItem>
            )}
            {visiblePages.map((p) => (
              <PaginationItem key={p}>
                <PaginationLink
                  isActive={p === page}
                  onClick={() => setPage(p)}
                >
                  {p}
                </PaginationLink>
              </PaginationItem>
            ))}
            {visiblePages[visiblePages.length - 1] < totalPages && (
              <PaginationItem>
                <PaginationEllipsis />
              </PaginationItem>
            )}
            <PaginationItem>
              <PaginationNext
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                aria-disabled={page >= totalPages}
                tabIndex={page >= totalPages ? -1 : undefined}
                className={
                  page >= totalPages ? "pointer-events-none opacity-50" : ""
                }
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      )}
    </PageContainer>
  );
}
