import { useParams } from "react-router-dom";
import { ExternalLinkIcon, PencilIcon } from "lucide-react";

import { useCurationJob } from "@/hooks/use-curation-job";
import { usePageParam } from "@/hooks/use-page-param";
import { useJobNeighbors, fetchCurationPage } from "@/hooks/use-job-neighbors";
import { CurationPairRow } from "@/components/curation/CurationPairRow";
import { JobImagePanel } from "@/components/curation/JobImagePanel";
import { JobNavButtons } from "@/components/curation/JobNavButtons";
import { PageContainer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { curationJobState, CURATION_STATE_LABELS } from "@/utils/curation";

export default function CurationJobPage() {
  const { jobId } = useParams();
  const numericId = jobId ? Number(jobId) : undefined;
  const { page } = usePageParam();
  const { job, loading, error, patchPair, reviewJob } =
    useCurationJob(numericId);
  // numericId가 undefined면 훅이 조회 자체를 하지 않는다.
  const {
    prev,
    next,
    loading: neighborsLoading,
  } = useJobNeighbors({
    jobId: numericId,
    page,
    fetchPage: fetchCurationPage,
  });

  // 검수 완료 후 목록으로 튕기지 않는다 — reviewJob이 POST 성공 시 curation_reviewed를
  // 로컬에 반영하므로(silent 재조회 실패와 무관하게) 버튼이 스스로 비활성되고,
  // 사용자는 이전/다음으로 계속 검수할 수 있다.
  const handleReview = async () => {
    await reviewJob();
  };

  // 잡 데이터에 의존하지 않으므로 세 분기 모두에 같은 요소를 건다. (1) 상세 조회가
  // 실패하면 이 화면에는 목록으로 돌아갈 UI가 하나도 없고(CurationTabs도 전역 내비의
  // 큐레이션 링크도 없다), (2) "다음 →"으로 이동하면 loading 분기로 되돌아가는데
  // 라우트에 key가 없어 element가 재사용되므로, 성공 분기에만 두면 방금 누른 버튼이
  // 언마운트돼 포커스가 body로 유실된다 — 연속 검수 이동과 정면 충돌한다.
  const nav = (
    <JobNavButtons
      basePath="/curation"
      page={page}
      prev={prev}
      next={next}
      loading={neighborsLoading}
    />
  );

  if (loading) {
    return (
      <PageContainer className="py-4">
        {nav}
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-64 w-full" />
      </PageContainer>
    );
  }

  if (error || !job || numericId === undefined) {
    return (
      <PageContainer className="py-4">
        {nav}
        <p className="text-destructive text-center text-sm">
          {error ?? "잡을 찾을 수 없습니다"}
        </p>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="py-4">
      {nav}
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          잡 #{job.job_id}
          {job.invoice_id != null && (
            <span className="text-muted-foreground ml-2 text-sm">
              (inv·{job.invoice_id})
            </span>
          )}
        </h1>
        <div className="flex items-center gap-2">
          {/* 검수 흐름(이전/다음 잡 이동)을 끊지 않도록 새 탭으로 연다. */}
          {job.invoice_id != null && (
            <Button variant="outline" asChild>
              <a
                href={`/edit/${job.invoice_id}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                <PencilIcon aria-hidden="true" />
                명세서 수정
                <ExternalLinkIcon aria-hidden="true" />
              </a>
            </Button>
          )}
          <Button onClick={handleReview} disabled={job.curation_reviewed}>
            검수 완료
          </Button>
        </div>
      </div>

      {/* 라이브 리전은 이미 DOM에 있는 요소의 변경만 통지한다 — 영역을 내용과 함께
          삽입하면 쌍 수정 직후 뜨는 이 배너가 SR에 안 읽힌다. 컨테이너는 상시
          마운트하고 내용만 토글한다. */}
      <div aria-live="polite">
        {curationJobState(job) === "needs_recheck" && (
          <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <p className="font-bold">{CURATION_STATE_LABELS.needs_recheck}</p>
            <p>
              수정 내용이 저장됐지만, &quot;검수 완료&quot;를 누르기 전까지 이
              잡의 학습쌍{" "}
              {job.pairs.filter((p) => p.status === "included").length}개가
              학습에서 제외됩니다.
            </p>
          </div>
        )}
      </div>

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
