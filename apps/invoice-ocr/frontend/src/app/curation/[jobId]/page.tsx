import { useNavigate, useParams } from "react-router-dom";

import { useCurationJob } from "@/hooks/use-curation-job";
import { CurationPairRow } from "@/components/curation/CurationPairRow";
import { JobImagePanel } from "@/components/curation/JobImagePanel";
import { PageContainer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { curationJobState } from "@/utils/curation";

export default function CurationJobPage() {
  const { jobId } = useParams();
  const numericId = jobId ? Number(jobId) : undefined;
  const navigate = useNavigate();
  const { job, loading, error, patchPair, reviewJob } =
    useCurationJob(numericId);

  const handleReview = async () => {
    const ok = await reviewJob();
    if (ok) navigate("/curation");
  };

  if (loading) {
    return (
      <PageContainer className="py-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-64 w-full" />
      </PageContainer>
    );
  }

  if (error || !job || numericId === undefined) {
    return (
      <PageContainer className="py-4">
        <p className="text-destructive text-center text-sm">
          {error ?? "잡을 찾을 수 없습니다"}
        </p>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="py-4">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          잡 #{job.job_id}
          {job.invoice_id != null && (
            <span className="text-muted-foreground ml-2 text-sm">
              (inv·{job.invoice_id})
            </span>
          )}
        </h1>
        <Button onClick={handleReview} disabled={job.curation_reviewed}>
          검수 완료
        </Button>
      </div>

      {curationJobState(job) === "needs_recheck" && (
        <div
          className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
          aria-live="polite"
        >
          <p className="font-bold">↺ 재확인 필요</p>
          <p>
            수정 내용이 저장됐지만, &quot;검수 완료&quot;를 누르기 전까지 이
            잡의 학습쌍{" "}
            {job.pairs.filter((p) => p.status === "included").length}개가
            학습에서 제외됩니다.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* 좌: 단계 이미지 (확정 전 상세와 공유) */}
        <JobImagePanel jobId={job.job_id} />

        {/* 우: 행별 학습쌍 */}
        <div>
          <h2 className="mb-2 text-sm font-semibold">행별 학습쌍</h2>
          {job.pairs.map((pair) => (
            <CurationPairRow
              key={pair.id}
              jobId={job.job_id}
              pair={pair}
              onPatch={patchPair}
            />
          ))}
        </div>
      </div>
    </PageContainer>
  );
}
