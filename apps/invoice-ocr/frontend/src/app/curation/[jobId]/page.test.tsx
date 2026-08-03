import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import CurationJobPage from "./page";
import { useCurationJob } from "@/hooks/use-curation-job";
import type { CurationJobDetail } from "@/types/curation";

vi.mock("@/hooks/use-curation-job", () => ({ useCurationJob: vi.fn() }));
vi.mock("@/hooks/use-items", () => ({ useItems: () => ({ data: [] }) }));
const mockHook = vi.mocked(useCurationJob);

function job(over: Partial<CurationJobDetail> = {}): CurationJobDetail {
  return {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    warp_ok: false,
    created_at: "2026-06-30T09:00:00",
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
        top5: [{ label: "무", sim: 0.77 }],
      },
    ],
    ...over,
  };
}

function setup(jobData: CurationJobDetail | null) {
  mockHook.mockReturnValue({
    job: jobData,
    loading: false,
    error: null,
    patchPair: vi.fn(),
    reviewJob: vi.fn().mockResolvedValue(true),
    refetch: vi.fn(),
  });
  return render(
    <MemoryRouter initialEntries={["/curation/128"]}>
      <Routes>
        <Route path="/curation/:jobId" element={<CurationJobPage />} />
      </Routes>
    </MemoryRouter>,
  );
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
});
