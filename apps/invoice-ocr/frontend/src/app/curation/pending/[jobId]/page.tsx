import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";

import { JobImagePanel } from "@/components/curation/JobImagePanel";
import { JobNavButtons } from "@/components/curation/JobNavButtons";
import { PageContainer } from "@/components/layout";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageParam } from "@/hooks/use-page-param";
import {
  useJobNeighbors,
  fetchUnconfirmedPage,
} from "@/hooks/use-job-neighbors";
import { ocrAPI, ocrCropUrl } from "@/services/api";
import type { OcrJobStatus, OcrResultRow, OcrItemPred } from "@/types/ocr";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";

const CROP_PLACEHOLDER = placeholderSvg(240, 48);
const handleImageError = fallbackToPlaceholder(CROP_PLACEHOLDER);

// 404(그 잡이 없다)와 그 외 실패(네트워크·500·데이터 루트 오설정 A6)를 갈라야 한다 —
// 관측 도구가 운영 장애를 "데이터 없음"으로 오진하면 안 된다.
// (use-curation-job.ts:20 errorMessage와 같은 관용구)
function fetchErrorMessage(e: unknown): string {
  if (axios.isAxiosError(e) && e.response?.status === 404) {
    return "잡을 찾을 수 없습니다";
  }
  return e instanceof Error ? e.message : "잡을 불러올 수 없습니다";
}

// 확정 전 상세는 확정 후 상세(app/curation/[jobId])와 파일부터 분리한다 —
// ADR 0009의 관문/비관문 경계를 코드 경계로 남긴다. 여기에는 조작이 하나도 없다.
export default function UnconfirmedJobDetailPage() {
  const { jobId } = useParams();
  // useParams는 문자열만 준다 — Number("")가 0(유효 id)으로 접히는 함정을 함께 닫는다.
  const numericId = jobId ? Number(jobId) : NaN;
  const hasValidId = !Number.isNaN(numericId);
  const [job, setJob] = useState<OcrJobStatus | null>(null);
  // 숫자가 아닌 jobId는 조회 자체를 하지 않으므로 로딩으로 시작하면 영영 끝나지 않는다
  // (use-curation-job.ts:28 `useState(!!jobId)`와 같은 idiom).
  const [loading, setLoading] = useState(hasValidId);
  const [error, setError] = useState<string | null>(null);
  // 형제 훅 use-unconfirmed-jobs.ts와 같은 idiom — 언마운트·jobId 교체 후 도착하는
  // in-flight 응답을 stale로 버린다(effect 본문 동기 setState도 함께 피한다).
  const reqId = useRef(0);
  const { page } = usePageParam();
  // numericId는 잘못된 jobId에서 NaN이 될 수 있는데, 훅이 그것도 흡수해 조회하지 않는다.
  const {
    prev,
    next,
    loading: neighborsLoading,
  } = useJobNeighbors({
    jobId: numericId,
    page,
    fetchPage: fetchUnconfirmedPage,
  });

  const fetchJob = useCallback(async () => {
    if (!hasValidId) return;
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await ocrAPI.getJob(numericId);
      if (myId !== reqId.current) return;
      setJob(res.data);
    } catch (e) {
      if (myId !== reqId.current) return;
      setError(fetchErrorMessage(e));
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [numericId, hasValidId]);

  useEffect(() => {
    fetchJob();
    return () => {
      // cleanup은 '가장 최근' 발행된 요청까지 무효화해야 한다 — ref 최신값을 읽는 것이 의도다.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      reqId.current++;
    };
  }, [fetchJob]);

  // 확정 후 상세와 같은 형태 — 잡 데이터에 의존하지 않으므로 세 분기 모두에 건다.
  // (1) 조회 실패 시 목록으로 돌아갈 UI가 하나도 없고, (2) "다음 →" 이동은 loading
  // 분기로 되돌아가는데 라우트에 key가 없어 element가 재사용되므로 성공 분기에만 두면
  // 방금 누른 버튼이 언마운트돼 포커스가 body로 유실된다.
  const nav = (
    <JobNavButtons
      basePath="/curation/pending"
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

  if (error !== null || job === null) {
    return (
      <PageContainer className="py-4">
        {nav}
        <p className="text-destructive text-center text-sm">
          {error ?? "잡을 찾을 수 없습니다"}
        </p>
      </PageContainer>
    );
  }

  // GET /ocr/jobs/{id}는 result_json을 그대로 반환하고(OcrService.get_job) OcrResult.rows는
  // 필수 배열·item_top5는 필수 객체 배열이라 단정하지만, 워커가 쓴 외부 데이터라 어느 층도
  // 보장되지 않는다 — 배열 여부만이 아니라 **원소까지** 런타임에 닫는다
  // (curation_service.py:63-65가 백엔드에서 쓰는 것과 같은 관용구: 배열 + isinstance(r, dict)).
  const rawRows: unknown = job.result?.rows;
  // row_index는 크롭 URL(ocrCropUrl)과 alt에 그대로 들어가므로 숫자까지 확인한다 —
  // 빠지거나 문자열이면 /crop/undefined를 요청하고 "undefined행 크롭"을 읽어준다.
  const rows = (Array.isArray(rawRows) ? rawRows : []).filter(
    (r): r is OcrResultRow =>
      typeof r === "object" && r !== null && typeof r.row_index === "number",
  );

  return (
    <PageContainer className="py-4">
      {nav}
      {/* 이 라우트는 미확정 여부를 검사하지 않는다 — GET /ocr/jobs/{id}가 invoice_id·correction
          존재를 주지 않고(OcrService.get_job), 상세용 엔드포인트 추가는 spec.md:93이 금지한다.
          그래서 확정 여부를 주장하지 않는 중립 표현을 쓴다(읽기 전용은 이 페이지의 구조적 사실이라 유지). */}
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          잡 #{job.id}
          <span className="text-muted-foreground ml-2 text-sm">
            {job.status} · 읽기 전용
          </span>
        </h1>
        {job.error !== undefined && (
          <span className="text-destructive text-sm">{job.error}</span>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* 좌: 원본·워프. "강등이냐 워프 산출이 없느냐"는 워프 이미지가 뜨는지가 답한다. */}
        <JobImagePanel jobId={job.id} />

        {/* 우: 초안 행 — 크롭 + top5 + 미확신 배지. 전부 읽기 전용. */}
        <div>
          <h2 className="mb-2 text-sm font-semibold">초안 행</h2>
          {rows.length === 0 && (
            <p className="text-muted-foreground text-sm">초안 행이 없습니다</p>
          )}
          {rows.map((row) => {
            const candidates = (
              Array.isArray(row.item_top5) ? row.item_top5 : []
            ).filter(
              (c): c is OcrItemPred =>
                typeof c === "object" &&
                c !== null &&
                typeof c.sim === "number" &&
                typeof c.label === "string",
            );
            return (
              <div
                key={row.row_index}
                className="flex items-center gap-3 border-b py-2"
              >
                <img
                  src={ocrCropUrl(job.id, row.row_index)}
                  alt={`${row.row_index}행 크롭`}
                  className="h-10 w-40 rounded border object-contain"
                  onError={handleImageError}
                />
                <div className="min-w-0 flex-1 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {candidates[0]?.label ?? "—"}
                    </span>
                    {row.item_uncertain === true && (
                      <span className="text-xs text-amber-600">미확신</span>
                    )}
                  </div>
                  <p className="text-muted-foreground truncate text-xs">
                    {candidates
                      .map((c) => `${c.label} ${c.sim.toFixed(2)}`)
                      .join(" · ") || "후보 없음"}
                  </p>
                </div>
                <div className="shrink-0 text-right text-sm">
                  <div>
                    {typeof row.supply === "number"
                      ? row.supply.toLocaleString()
                      : "—"}
                  </div>
                  {/* 금액 OCR 원문 — 병합("160+40+30")·재시도("→")·하단 절단("(cont×N 절단)")의
                      유일한 화면 노출 지점(#39 §2.1). 워커가 쓴 외부 문자열이므로 파싱하지 않고
                      그대로 보여준다(amount_raw는 표시·전달 전용 계약). title은 테스트가 이 줄을
                      집는 안정적 손잡이이자 화면의 설명이다. */}
                  {typeof row.amount_raw === "string" &&
                    row.amount_raw !== "" && (
                      <div
                        title="금액 OCR 원문"
                        className="text-muted-foreground max-w-[12rem] truncate text-xs"
                      >
                        {row.amount_raw}
                      </div>
                    )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </PageContainer>
  );
}
