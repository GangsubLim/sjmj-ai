import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import UnconfirmedJobDetailPage from "./page";
import { ocrAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  ocrAPI: { getJob: vi.fn() },
  curationImageUrl: (jobId: number, kind: string) =>
    `/api/curation/jobs/${jobId}/image/${kind}`,
  ocrCropUrl: (jobId: number, row: number) =>
    `/api/ocr/jobs/${jobId}/crop/${row}`,
}));

const mockGetJob = vi.mocked(ocrAPI.getJob);

function renderDetail(jobId = "42") {
  const router = createMemoryRouter(
    [
      {
        path: "/curation/pending/:jobId",
        element: <UnconfirmedJobDetailPage />,
      },
    ],
    { initialEntries: [`/curation/pending/${jobId}`] },
  );
  return render(<RouterProvider router={router} />);
}

describe("UnconfirmedJobDetailPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("원본·워프 이미지와 초안 행을 읽기 전용으로 그린다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: {
          rows: [
            {
              row_index: 0,
              crop_ref: "job-42/row-0",
              item_top5: [
                { label: "삼겹살", sim: 0.9 },
                { label: "목살", sim: 0.4 },
              ],
              supply: 100000,
              amount_raw: "100000",
              item_uncertain: true,
            },
          ],
          supply_sum: 100000,
          warp_ok: true,
        },
      },
    });

    renderDetail();

    await waitFor(() =>
      expect(screen.getByAltText("원본 전표")).toBeInTheDocument(),
    );
    expect(screen.getByAltText("워프 전표")).toBeInTheDocument();
    expect(screen.getByText("삼겹살")).toBeInTheDocument();
    expect(screen.getByText("미확신")).toBeInTheDocument();
    // 읽기 전용 — 어떤 조작 요소도 없다(ADR 0009).
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("rows가 null이어도 런타임 오류 없이 그린다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        // 워커가 쓴 외부 데이터 — 타입이 단정하는 배열이 아닐 수 있다.
        result: { rows: null, supply_sum: 0, warp_ok: true },
      },
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getJob>>);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByText("초안 행이 없습니다")).toBeInTheDocument(),
    );
    expect(screen.getByAltText("워프 전표")).toBeInTheDocument();
  });

  it("rows 원소가 null이어도 런타임 오류 없이 그린다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: { rows: [null], supply_sum: 0, warp_ok: true },
      },
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getJob>>);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByText("초안 행이 없습니다")).toBeInTheDocument(),
    );
  });

  it("item_top5가 배열이 아니거나 sim이 숫자가 아니어도 죽지 않는다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: {
          rows: [
            { row_index: 0, item_top5: {}, supply: 1 },
            {
              row_index: 1,
              item_top5: [{ label: "삼겹살", sim: "0.9" }],
              supply: 2,
            },
          ],
          supply_sum: 3,
          warp_ok: true,
        },
      },
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getJob>>);

    renderDetail();

    // 두 행 모두 그려지고(0행/1행 크롭 alt), 후보가 없으면 "후보 없음"으로 닫힌다.
    await waitFor(() =>
      expect(screen.getByAltText("0행 크롭")).toBeInTheDocument(),
    );
    expect(screen.getByAltText("1행 크롭")).toBeInTheDocument();
    expect(screen.getAllByText("후보 없음")).toHaveLength(2);
  });

  it("row_index가 숫자가 아닌 행은 undefined 크롭을 요청하지 않는다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: {
          // 워커가 쓴 외부 데이터 — row_index가 없거나 문자열일 수 있다.
          rows: [
            { item_top5: [], supply: 1 },
            { row_index: "1", item_top5: [], supply: 2 },
            { row_index: 2, item_top5: [], supply: 3 },
          ],
          supply_sum: 6,
          warp_ok: true,
        },
      },
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getJob>>);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByAltText("2행 크롭")).toBeInTheDocument(),
    );
    expect(screen.queryByAltText("undefined행 크롭")).not.toBeInTheDocument();
    expect(screen.queryByAltText("1행 크롭")).not.toBeInTheDocument();
    const crops = screen
      .getAllByRole("img")
      .map((img) => img.getAttribute("src") ?? "");
    expect(crops.some((src) => src.includes("/crop/undefined"))).toBe(false);
  });

  it("supply를 CurationPairRow와 같은 천단위 구분 기호로 그린다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: {
          rows: [
            {
              row_index: 0,
              crop_ref: "job-42/row-0",
              item_top5: [],
              supply: 100000,
              amount_raw: "100000",
              item_uncertain: false,
            },
          ],
          supply_sum: 100000,
          warp_ok: true,
        },
      },
    });

    renderDetail();

    await waitFor(() =>
      expect(screen.getByText("100,000")).toBeInTheDocument(),
    );
  });

  it("supply가 객체·label이 배열이어도 죽지 않고 —/후보 없음으로 닫힌다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: {
          rows: [
            {
              row_index: 0,
              // 워커가 쓴 외부 데이터 — supply·label이 원시값이 아닐 수 있다.
              item_top5: [{ label: ["삼겹살"], sim: 0.9 }],
              supply: { amount: 100000 },
            },
          ],
          supply_sum: 0,
          warp_ok: true,
        },
      },
    } as unknown as Awaited<ReturnType<typeof ocrAPI.getJob>>);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByAltText("0행 크롭")).toBeInTheDocument(),
    );
    expect(screen.getByText("후보 없음")).toBeInTheDocument();
    // 라벨(candidates[0]?.label)·supply 두 자리 모두 원시값이 아니라 "—"로 닫힌다.
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("실패 잡은 error 문자열을 보인다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: { id: 42, status: "failed", error: "warp 실패" },
    });

    renderDetail();

    await waitFor(() =>
      expect(screen.getByText("warp 실패")).toBeInTheDocument(),
    );
  });

  it("404면 잡을 찾을 수 없다고 알린다", async () => {
    mockGetJob.mockRejectedValue(
      Object.assign(new Error("Request failed"), {
        isAxiosError: true,
        response: { status: 404 },
      }),
    );
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("잡을 찾을 수 없습니다")).toBeInTheDocument(),
    );
  });

  it("숫자가 아닌 jobId는 조회하지 않고 찾을 수 없다고 알린다", async () => {
    renderDetail("abc");

    await waitFor(() =>
      expect(screen.getByText("잡을 찾을 수 없습니다")).toBeInTheDocument(),
    );
    expect(mockGetJob).not.toHaveBeenCalled();
  });

  it("500·네트워크 장애는 404 문구로 위장하지 않는다", async () => {
    mockGetJob.mockRejectedValue(new Error("Network Error"));
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("Network Error")).toBeInTheDocument(),
    );
    expect(screen.queryByText("잡을 찾을 수 없습니다")).not.toBeInTheDocument();
  });
});
