import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { AxiosError, AxiosHeaders } from "axios";

import { useJobGeometry } from "@/hooks/use-job-geometry";
import { curationAPI } from "@/services/api";
import type { StageGeometry } from "@/types/curation";

vi.mock("@/services/api", () => ({
  curationAPI: { getGeometry: vi.fn() },
}));

const GEOMETRY: StageGeometry = {
  version: 1,
  generation: 0,
  image_size: [4032, 3024],
  warp_size: [900, 2100],
  quad: [
    [0, 0],
    [10, 0],
    [10, 20],
    [0, 20],
  ],
  quad_source: "color",
  deskew_deg: 0.42,
};

function httpError(status: number): AxiosError {
  return new AxiosError("boom", "ERR_BAD_REQUEST", undefined, null, {
    status,
    statusText: "",
    data: {},
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  });
}

const getGeometry = vi.mocked(curationAPI.getGeometry);

beforeEach(() => {
  getGeometry.mockReset();
});

describe("useJobGeometry", () => {
  it("200이면 문서를 그대로 싣는다", async () => {
    getGeometry.mockResolvedValue({ success: true, data: GEOMETRY });

    const { result } = renderHook(() => useJobGeometry(7));

    await waitFor(() =>
      expect(result.current).toEqual({ status: "ready", geometry: GEOMETRY }),
    );
  });

  it("404는 관측 없음이다 — 오류가 아니라 폴백 신호다", async () => {
    getGeometry.mockRejectedValue(httpError(404));

    const { result } = renderHook(() => useJobGeometry(7));

    await waitFor(() => expect(result.current.status).toBe("absent"));
  });

  it("409는 이전 세대 기하다 — 판정 입력을 잠그는 신호로 따로 센다", async () => {
    getGeometry.mockRejectedValue(httpError(409));

    const { result } = renderHook(() => useJobGeometry(7));

    await waitFor(() => expect(result.current.status).toBe("stale"));
  });

  it("500은 손상이다 — 조용한 폴백으로 뭉개지 않는다", async () => {
    getGeometry.mockRejectedValue(httpError(500));

    const { result } = renderHook(() => useJobGeometry(7));

    await waitFor(() => expect(result.current.status).toBe("corrupt"));
  });

  it("mock 모드가 아니면 axios가 아닌 예외도 error다", async () => {
    vi.stubEnv("VITE_USE_MOCK", "false");
    getGeometry.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useJobGeometry(7));

    await waitFor(() => expect(result.current.status).toBe("error"));

    vi.unstubAllEnvs();
  });

  it("jobId가 없으면 조회 자체를 하지 않는다", () => {
    renderHook(() => useJobGeometry(undefined));

    expect(getGeometry).not.toHaveBeenCalled();
  });

  it("늦게 도착한 옛 잡의 응답이 새 잡의 상태를 덮지 않는다", async () => {
    // "다음 잡" 이동은 컴포넌트를 언마운트하지 않고 jobId만 바꾼다 — 옛 응답이 이기면
    // 사람이 지금 보는 전표와 다른 잡의 기하를 겹쳐 보고 판정한다.
    let resolveOld: (v: {
      success: boolean;
      data: StageGeometry;
    }) => void = () => {};
    getGeometry.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    getGeometry.mockRejectedValueOnce(httpError(404));

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useJobGeometry(id),
      { initialProps: { id: 1 } },
    );
    rerender({ id: 2 });
    await waitFor(() => expect(result.current.status).toBe("absent"));

    resolveOld({ success: true, data: GEOMETRY });
    // waitFor는 첫 호출이 동기라 이미 absent인 상태에서는 가드를 지워도 통과함 — 마이크로
    // 태스크를 비운 뒤 재확인해야 옛 응답의 덮어쓰기가 실제로 걸린다.
    await act(async () => {});
    expect(result.current.status).toBe("absent");
  });
});
