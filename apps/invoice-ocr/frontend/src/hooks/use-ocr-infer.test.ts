import { renderHook, act, waitFor } from "@testing-library/react";
import { useOcrInfer } from "./use-ocr-infer";
import { ocrAPI } from "@/services/api";

vi.mock("@/services/api", () => ({
  ocrAPI: { createJob: vi.fn(), getJob: vi.fn(), confirm: vi.fn() },
}));

describe("useOcrInfer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uploads then polls until done", async () => {
    (ocrAPI.createJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { job_id: 1, status: "pending" },
    });
    (ocrAPI.getJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ data: { id: 1, status: "running" } })
      .mockResolvedValueOnce({
        data: {
          id: 1,
          status: "done",
          result: { rows: [], supply_sum: 0, warp_ok: true },
        },
      });

    const { result } = renderHook(() => useOcrInfer());
    await act(async () => {
      await result.current.upload(new File([new Uint8Array([1])], "x.jpg"));
    });
    await waitFor(() => expect(result.current.status).toBe("done"), {
      timeout: 8000,
    });
    expect(result.current.result?.warp_ok).toBe(true);
  });

  it("sets status to failed and re-throws when createJob rejects", async () => {
    (ocrAPI.createJob as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network error"),
    );

    const { result } = renderHook(() => useOcrInfer());
    await act(async () => {
      await expect(
        result.current.upload(new File([new Uint8Array([1])], "x.jpg")),
      ).rejects.toThrow("Network error");
    });
    expect(result.current.status).toBe("failed");
    expect(result.current.error).toBe("Network error");
  });
});
