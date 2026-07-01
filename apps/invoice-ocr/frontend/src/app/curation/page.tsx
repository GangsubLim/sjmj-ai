import { useNavigate } from "react-router-dom";
import { InboxIcon } from "lucide-react";

import { useCurationJobs } from "@/hooks/use-curation-jobs";
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

function formatDate(iso: string): string {
  return iso.slice(5, 10); // MM-DD
}

export default function CurationQueuePage() {
  const navigate = useNavigate();
  const { data, total, page, totalPages, loading, error, setPage } =
    useCurationJobs(20);
  const visiblePages = getVisiblePages(page, totalPages);

  const goToJob = (jobId: number) => navigate(`/curation/${jobId}`);

  return (
    <PageContainer className="py-4">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold">OCR 학습 큐레이션</h1>
        <span className="text-muted-foreground text-sm">총 {total}건</span>
      </div>

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
          title="검수할 잡이 없습니다"
          description="confirmed된 OCR 잡이 생기면 여기에 표시됩니다."
        />
      )}

      {!loading && !error && data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground border-b text-left">
            <tr>
              <th className="py-2">잡</th>
              <th>명세서</th>
              <th>행수</th>
              <th>미처리</th>
              <th>상태</th>
              <th>생성일</th>
            </tr>
          </thead>
          <tbody>
            {/* 행 전체는 마우스 편의용 onClick, 키보드·SR 진입점은 셀 내부 네이티브 button(테이블 행/셀 시맨틱 보존) */}
            {data.map((job) => (
              <tr
                key={job.job_id}
                className="hover:bg-muted/50 cursor-pointer border-b"
                onClick={() => goToJob(job.job_id)}
              >
                <td className="py-2 font-medium">
                  <button
                    type="button"
                    aria-label={`잡 #${job.job_id} 상세`}
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
                  {job.invoice_id != null ? `inv·${job.invoice_id}` : "—"}
                </td>
                <td>{job.pair_count}</td>
                <td>{job.unreviewed_count}</td>
                <td>
                  {job.curation_reviewed ? (
                    <span className="text-green-600">✓ 검수됨</span>
                  ) : (
                    <span className="text-amber-600">● 미검수</span>
                  )}
                </td>
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
