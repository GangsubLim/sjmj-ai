import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import UnconfirmedJobDetailPage from "./page";
import { ocrAPI } from "@/services/api";
import {
  useJobNeighbors,
  fetchUnconfirmedPage,
} from "@/hooks/use-job-neighbors";

vi.mock("@/services/api", () => ({
  ocrAPI: { getJob: vi.fn() },
  curationImageUrl: (jobId: number, kind: string) =>
    `/api/curation/jobs/${jobId}/image/${kind}`,
  ocrCropUrl: (jobId: number, row: number) =>
    `/api/ocr/jobs/${jobId}/crop/${row}`,
}));
// 이웃 조회는 이 화면의 부가 기능이라 여기서는 결과만 주입한다(실 API 호출 차단).
vi.mock("@/hooks/use-job-neighbors", () => ({
  useJobNeighbors: vi.fn(),
  fetchUnconfirmedPage: vi.fn(),
}));

const mockGetJob = vi.mocked(ocrAPI.getJob);
const mockNeighbors = vi.mocked(useJobNeighbors);

function renderDetail(jobId = "42", entry?: string) {
  // beforeEach의 vi.clearAllMocks()가 반환값도 지우므로 렌더 직전에 매번 세팅한다.
  mockNeighbors.mockReturnValue({ prev: null, next: null, loading: false });
  const router = createMemoryRouter(
    [
      {
        path: "/curation/pending/:jobId",
        element: <UnconfirmedJobDetailPage />,
      },
      { path: "/curation/pending", element: <div>확정 전 목록</div> },
    ],
    { initialEntries: [entry ?? `/curation/pending/${jobId}`] },
  );
  return { ...render(<RouterProvider router={router} />), router };
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
    // 읽기 전용 — 도메인 조작 요소는 없다(ADR 0009). 잡 이동(JobNavButtons)은
    // 도메인 데이터를 건드리지 않는 순수 네비게이션이라 예외로 허용한다.
    expect(screen.queryAllByRole("button").map((b) => b.textContent)).toEqual([
      "← 목록",
      "← 이전",
      "다음 →",
    ]);
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

  it("이전/다음 버튼이 렌더되고 이웃이 없으면 비활성이다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: { rows: [], supply_sum: 0, warp_ok: true },
      },
    });

    renderDetail();

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "← 이전" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "← 이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeDisabled();
    // 확정 전 화면은 반드시 fetchUnconfirmedPage를 넘겨야 한다 — 어댑터가 바뀌어도
    // 반환값 주입만으로는 GREEN이 되므로 호출 인자를 계약으로 고정한다.
    expect(mockNeighbors).toHaveBeenCalledWith({
      jobId: 42,
      page: 1,
      fetchPage: fetchUnconfirmedPage,
    });
  });

  it("상세 조회 중에도 잡 이동 버튼이 남아 있다", () => {
    // "다음 →"으로 jobId가 바뀌면 loading 분기로 되돌아가는데 라우트에 key가 없어
    // element가 재사용된다 — 성공 분기에만 두면 방금 누른 버튼이 언마운트돼 포커스가
    // 유실된다(확정 후 상세와 같은 이유).
    mockGetJob.mockReturnValue(new Promise(() => {})); // 영원히 pending — 로딩 분기 고정
    renderDetail();
    expect(screen.getByRole("button", { name: "← 목록" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeInTheDocument();
  });

  it("상세 조회에 실패해도 목록 버튼으로 탈출할 수 있다", async () => {
    // 이 화면에는 목록으로 돌아갈 다른 UI가 없다 — 에러 분기에도 nav가 있어야 한다.
    mockGetJob.mockRejectedValue(new Error("Network Error"));
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("Network Error")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "← 목록" })).toBeInTheDocument();
  });

  it("목록 버튼이 page를 유지한 확정 전 목록으로 간다", async () => {
    mockGetJob.mockResolvedValue({
      success: true,
      data: {
        id: 42,
        status: "done",
        result: { rows: [], supply_sum: 0, warp_ok: true },
      },
    });

    const { router } = renderDetail("42", "/curation/pending/42?page=2");

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "← 목록" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));

    await waitFor(() =>
      expect(screen.getByText("확정 전 목록")).toBeInTheDocument(),
    );
    expect(router.state.location.search).toBe("?page=2");
  });
});
