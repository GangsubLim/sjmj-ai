import { renderHook, act } from "@testing-library/react";
import { useCaptureShare } from "./use-capture-share";

vi.mock("html-to-image", () => ({
  toBlob: vi.fn(),
}));
import { toBlob } from "html-to-image";
const mToBlob = vi.mocked(toBlob);

describe("useCaptureShare", () => {
  let node: HTMLDivElement;

  beforeEach(() => {
    vi.clearAllMocks();
    node = document.createElement("div");
    document.body.appendChild(node);
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
  });

  afterEach(() => {
    node.remove();
  });

  it("기본 경로(preferShare 없음) → toBlob 후 download 트리거", async () => {
    mToBlob.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
      }),
    );
    await act(async () => {
      await result.current.capture();
    });
    expect(clickSpy).toHaveBeenCalled();
    expect(result.current.isCapturing).toBe(false);
  });

  it("toBlob 실패 1회 → retry 후 download", async () => {
    mToBlob
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(new Blob(["x"], { type: "image/png" }));
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
      }),
    );
    await act(async () => {
      await result.current.capture();
    });
    expect(mToBlob).toHaveBeenCalledTimes(2);
    expect(clickSpy).toHaveBeenCalled();
  });

  it("Blob > sizeCap → pixelRatio 1로 재시도", async () => {
    const big = new Blob([new Uint8Array(9 * 1024 * 1024)], { type: "image/png" });
    const small = new Blob([new Uint8Array(1024)], { type: "image/png" });
    mToBlob.mockResolvedValueOnce(big).mockResolvedValueOnce(small);

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
        sizeCapBytes: 8 * 1024 * 1024,
        pixelRatio: 2,
      }),
    );
    await act(async () => {
      await result.current.capture();
    });
    expect(mToBlob.mock.calls[1]?.[1]).toMatchObject({ pixelRatio: 1 });
    expect(clickSpy).toHaveBeenCalled();
  });

  it("preferShare=true + canShare → navigator.share 호출", async () => {
    mToBlob.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    const shareMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: () => true,
    });
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: shareMock,
    });

    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
        preferShare: true,
      }),
    );
    await act(async () => {
      await result.current.capture();
    });
    expect(shareMock).toHaveBeenCalled();
  });

  it("beforeCapture/afterCapture wrap 호출 (parent transform 복원)", async () => {
    mToBlob.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const order: string[] = [];
    const before = vi.fn(() => {
      order.push("before");
      return "saved";
    });
    const after = vi.fn((s: unknown) => {
      order.push(`after:${s}`);
    });

    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
        beforeCapture: before,
        afterCapture: after,
      }),
    );
    await act(async () => {
      await result.current.capture();
    });
    expect(before).toHaveBeenCalledOnce();
    expect(after).toHaveBeenCalledWith("saved");
    expect(order).toEqual(["before", "after:saved"]);
  });

  it("toBlob throw 시에도 afterCapture 호출 (복원 보장)", async () => {
    mToBlob.mockRejectedValue(new Error("boom"));
    const after = vi.fn();
    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
        beforeCapture: () => "x",
        afterCapture: after,
      }),
    );
    await expect(
      act(async () => {
        await result.current.capture();
      }),
    ).rejects.toThrow();
    expect(after).toHaveBeenCalledWith("x");
  });

  it("share AbortError → silent", async () => {
    mToBlob.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    const abort = new Error("cancel");
    abort.name = "AbortError";
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: () => true,
    });
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: vi.fn().mockRejectedValue(abort),
    });

    const { result } = renderHook(() =>
      useCaptureShare({
        getNode: () => node,
        filename: "test.png",
        preferShare: true,
      }),
    );
    await expect(
      act(async () => {
        await result.current.capture();
      }),
    ).resolves.not.toThrow();
  });
});
