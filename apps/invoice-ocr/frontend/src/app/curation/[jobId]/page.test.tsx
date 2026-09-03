import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import CurationJobPage from "./page";
import { useCurationJob } from "@/hooks/use-curation-job";
import { useJobNeighbors, fetchCurationPage } from "@/hooks/use-job-neighbors";
import type { CurationJobDetail } from "@/types/curation";

vi.mock("@/hooks/use-curation-job", () => ({ useCurationJob: vi.fn() }));
vi.mock("@/hooks/use-items", () => ({ useItems: () => ({ data: [] }) }));
// 이웃 조회는 이 화면의 부가 기능이라 여기서는 결과만 주입한다(실 API 호출 차단).
vi.mock("@/hooks/use-job-neighbors", () => ({
  useJobNeighbors: vi.fn(),
  fetchCurationPage: vi.fn(),
}));
const mockHook = vi.mocked(useCurationJob);
const mockNeighbors = vi.mocked(useJobNeighbors);

function job(over: Partial<CurationJobDetail> = {}): CurationJobDetail {
  return {
    job_id: 128,
    invoice_id: 341,
    status: "done",
    curation_reviewed: false,
    curation_reviewed_at: null,
    warp_ok: false,
    created_at: "2026-06-30T09:00:00",
    job_token: "1000",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
        crop_available: true,
        top5: [{ label: "무", sim: 0.77 }],
      },
    ],
    ...over,
  };
}

// 검수됐다가 쌍 수정으로 해제된 잡 — 배너 대상.
// pairs에 included 1건 + excluded 1건을 섞어 둔다 — included만 세는 필터가
// 실제로 동작해야만 "학습쌍 1개"가 나온다(둘 다 세면 2개가 되어 테스트가 갈라진다).
function needsRecheckJob(): CurationJobDetail {
  return job({
    curation_reviewed: false,
    curation_reviewed_at: "2026-06-30T08:30:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        status: "included",
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
        crop_available: true,
        top5: [{ label: "무", sim: 0.77 }],
      },
      {
        id: 9002,
        crop_ref: "128/1",
        row_index: 1,
        draft_label: "당근",
        final_label: "당근",
        canonical_label: "당근",
        supply: 5000,
        status: "excluded",
        exclusion_reason: "blank_crop",
        reviewed_at: null,
        uncertain: false,
        crop_available: true,
        top5: [{ label: "당근", sim: 0.9 }],
      },
    ],
  });
}

function setup(
  jobData: CurationJobDetail | null,
  options: {
    entry?: string;
    reviewJob?: () => Promise<boolean>;
    neighbors?: ReturnType<typeof useJobNeighbors>;
    loading?: boolean;
    error?: string | null;
  } = {},
) {
  mockHook.mockReturnValue({
    job: jobData,
    loading: options.loading ?? false,
    error: options.error ?? null,
    patchPair: vi.fn(),
    reviewJob: options.reviewJob ?? vi.fn().mockResolvedValue(true),
    refetch: vi.fn(),
  });
  mockNeighbors.mockReturnValue(
    options.neighbors ?? { prev: null, next: null, loading: false },
  );
  const router = createMemoryRouter(
    [
      { path: "/curation/:jobId", element: <CurationJobPage /> },
      { path: "/curation", element: <div>목록</div> },
    ],
    { initialEntries: [options.entry ?? "/curation/128?page=3"] },
  );
  return { ...render(<RouterProvider router={router} />), router };
}

describe("CurationJobPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("잡 헤더와 행을 렌더한다", () => {
    setup(job());
    expect(screen.getByText(/잡 #128/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "검수 완료" }),
    ).toBeInTheDocument();
  });

  it("warp_ok=false여도 워프 이미지를 시도한다(분기 없음 — 404면 폴백)", () => {
    // 파일이 있는데도 가리던 결함을 고친 변경이다. 강등 잡의 warped.png는 디스크에
    // 존재하므로(ml/handwriting/infer_job.py:184가 게이트 판정보다 먼저 저장한다)
    // warp_ok=false에서 이미지를 안 그리면 볼 수 있는 그림을 UI가 가린다.
    setup(job({ warp_ok: false }));
    expect(screen.queryByText("워프 산출 없음")).not.toBeInTheDocument();
    expect(screen.getByAltText("워프 전표")).toBeInTheDocument();
  });

  it("이미지 로드 실패 시 placeholder로 degrade한다", () => {
    setup(job());
    const original = screen.getByAltText("원본 전표") as HTMLImageElement;
    fireEvent.error(original);
    expect(original.src).toContain("data:image/svg+xml");
  });

  it("재검수 필요 잡에 배너를 띄우고 검수 완료 버튼을 활성화한다", () => {
    setup(needsRecheckJob());
    expect(screen.getByText("↺ 재검수 필요")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "검수 완료" })).toBeEnabled();
  });

  it("배너는 학습에서 빠지는 included 쌍 개수를 명시한다", () => {
    // 잡 단위 게이트의 부작용(한 행 빼려다 잡 전체가 export에서 누락)을 숫자로 보여준다(AC 4).
    setup(needsRecheckJob());
    expect(screen.getByText(/학습쌍 1개/)).toBeInTheDocument();
  });

  it("배너는 쌍 수정 후 동적으로 나타나므로 aria-live=polite로 SR에 통지한다", () => {
    setup(needsRecheckJob());
    const banner = screen.getByText("↺ 재검수 필요").closest("[aria-live]");
    expect(banner).toBeInTheDocument();
  });

  it("배너가 없는 상태에서도 aria-live 영역은 DOM에 남아 있다", () => {
    // 라이브 리전은 "이미 DOM에 있는" 요소의 변경만 통지한다. 영역과 내용이 함께
    // 삽입되면 SR(NVDA/JAWS/VoiceOver)은 아무것도 읽지 않는다 — 쌍 수정 직후
    // 배너가 뜨는 이 화면이 정확히 그 경우라, 컨테이너는 상시 마운트해야 한다.
    const { container } = setup(job()); // 배너 없는 상태(unreviewed)
    expect(container.querySelector("[aria-live]")).toBeInTheDocument();
  });

  it("한 번도 검수 안 한 잡에는 배너를 띄우지 않는다", () => {
    setup(job()); // curation_reviewed=false, curation_reviewed_at=null
    expect(screen.queryByText("↺ 재검수 필요")).not.toBeInTheDocument();
  });

  it("검수된 잡의 검수 완료 버튼은 비활성이고 배너도 없다", () => {
    setup(
      job({
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      }),
    );
    expect(screen.queryByText("↺ 재검수 필요")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "검수 완료" })).toBeDisabled();
  });

  it("재처리 큐에 든 잡에 경고 배너를 띄운다", () => {
    setup(job({ status: "pending" }));
    expect(screen.getByText("⏳ 재처리 대기·진행 중")).toBeInTheDocument();
    expect(screen.getByText(/처리가 끝난 뒤 검수하세요/)).toBeInTheDocument();
  });

  it("처리에 실패한 잡에는 재처리 요청 문구 배너를 띄운다", () => {
    setup(job({ status: "failed" }));
    expect(screen.getByText("⚠ 처리 실패")).toBeInTheDocument();
    expect(
      screen.getByText(/재처리를 요청해 다시 시도하세요/),
    ).toBeInTheDocument();
    // 실패를 "대기·진행 중"으로 오인시키는 문구가 함께 뜨면 안 된다.
    expect(
      screen.queryByText("⏳ 재처리 대기·진행 중"),
    ).not.toBeInTheDocument();
  });

  it("done 잡에는 상태 배너가 없다", () => {
    setup(job());
    expect(
      screen.queryByText("⏳ 재처리 대기·진행 중"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("⚠ 처리 실패")).not.toBeInTheDocument();
  });

  it("배너는 경고일 뿐 편집·검수완료를 차단하지 않는다", () => {
    // 확정 결정 — 차단은 백엔드 409의 몫이고 배너는 그 예고다. 여기서 disabled를
    // 걸면 "저장이 안 되는 이유"를 화면이 두 곳에서 다르게 소유하게 된다.
    setup(job({ status: "pending" }));
    expect(screen.getByLabelText("행 0 라벨")).toBeEnabled();
    expect(screen.getByRole("button", { name: "검수 완료" })).toBeEnabled();
  });

  it("재처리 큐에 든 재검수 필요 잡에는 상태 배너만 띄운다(재검수 배너 억제)", () => {
    // patch_pair가 게이트를 해제한 뒤 재처리를 요청하면 실제로 나오는 조합이다
    // (release_gate는 curation_reviewed_at을 지우지 않고, requeue_for_reprocess는
    // status만 전이한다). 두 배너를 함께 띄우면 "저장됐다"와 "저장되지 않는다"가
    // 나란히 서고, 재검수 배너의 지시(검수 완료)는 409라 실행조차 못 한다.
    setup({ ...needsRecheckJob(), status: "pending" });
    expect(screen.getByText("⏳ 재처리 대기·진행 중")).toBeInTheDocument();
    expect(screen.queryByText("↺ 재검수 필요")).not.toBeInTheDocument();
  });

  it("이전/다음 버튼이 렌더되고 이웃이 없으면 비활성이다", () => {
    setup(job());
    expect(screen.getByRole("button", { name: "← 이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeDisabled();
    // 어댑터·jobId·page 배선까지 고정한다 — 반환값만 주입하면 확정 전 어댑터를
    // 넘기거나 page 대신 상수를 넘겨도 이 스위트가 전부 GREEN이 된다.
    expect(mockNeighbors).toHaveBeenCalledWith({
      jobId: 128,
      page: 3,
      fetchPage: fetchCurationPage,
    });
  });

  it("상세 조회 중에도 잡 이동 버튼이 남아 있다", () => {
    // "다음 →"을 누르면 jobId가 바뀌어 loading 분기로 되돌아가는데, 라우트에 key가 없어
    // element가 재사용된다 — 성공 분기에만 두면 방금 누른 버튼이 언마운트돼 포커스가
    // body로 유실되고 연속 검수 이동이 끊긴다.
    setup(null, { loading: true });
    expect(screen.getByRole("button", { name: "← 목록" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeInTheDocument();
  });

  it("상세 조회에 실패해도 목록 버튼으로 탈출할 수 있다", () => {
    // 이 화면에는 CurationTabs도 전역 내비의 큐레이션 링크도 없다 — nav가 없으면
    // 에러 텍스트만 남고 목록으로 돌아갈 UI가 하나도 없다.
    setup(null, { error: "조회 실패" });
    expect(screen.getByText("조회 실패")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← 목록" })).toBeInTheDocument();
  });

  it("목록 버튼이 page를 유지한 목록 URL로 간다", async () => {
    const { router } = setup(job(), { entry: "/curation/128?page=3" });

    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));

    await waitFor(() => expect(screen.getByText("목록")).toBeInTheDocument());
    expect(router.state.location.search).toBe("?page=3");
  });

  it("검수 완료 성공 후 목록으로 이동하지 않는다", async () => {
    // 상세에 머무는 쪽을 택했다 — reviewJob의 silent 재조회가 curation_reviewed를
    // 갱신하므로 버튼이 스스로 비활성되고 상태 배지가 따라 바뀐다.
    const reviewJob = vi.fn().mockResolvedValue(true);
    const { router } = setup(needsRecheckJob(), { reviewJob });

    fireEvent.click(screen.getByRole("button", { name: "검수 완료" }));

    await waitFor(() => expect(reviewJob).toHaveBeenCalledTimes(1));
    expect(router.state.location.pathname).toBe("/curation/128");
    expect(screen.queryByText("목록")).not.toBeInTheDocument();
  });

  it("연결된 명세서의 수정 화면을 새 탭 링크로 연다", () => {
    setup(job({ invoice_id: 341 }));
    const link = screen.getByRole("link", { name: /명세서 수정/ });
    expect(link).toHaveAttribute("href", "/edit/341");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("확정 전이라 invoice_id가 없으면 수정 링크를 걸지 않는다", () => {
    setup(job({ invoice_id: null }));
    expect(
      screen.queryByRole("link", { name: /명세서 수정/ }),
    ).not.toBeInTheDocument();
  });
});
