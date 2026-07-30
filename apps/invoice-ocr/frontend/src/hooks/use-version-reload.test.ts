import { renderHook, waitFor } from "@testing-library/react";
import { fetchServerVersion } from "@/services/api";
import { useVersionReload } from "./use-version-reload";

const routerState = vi.hoisted(() => ({ pathname: "/" }));

// 훅은 useLocation만 쓴다. MemoryRouter로는 renderHook에서 경로 전환을 만들 수 없어
// pathname을 직접 제어한다.
vi.mock("react-router-dom", () => ({
  useLocation: () => ({ pathname: routerState.pathname }),
}));

vi.mock("@/services/api", () => ({ fetchServerVersion: vi.fn() }));

const mockFetchServerVersion = vi.mocked(fetchServerVersion);

describe("useVersionReload", () => {
  let reload: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    routerState.pathname = "/";
    reload = vi.fn();
    // jsdom의 location.reload는 spyOn으로 대체할 수 없다(non-configurable).
    vi.stubGlobal("location", { ...window.location, reload });
    mockFetchServerVersion.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("최초 마운트에서는 서버 버전을 확인하지 않는다", () => {
    mockFetchServerVersion.mockResolvedValue(__APP_VERSION__);
    renderHook(() => useVersionReload());
    expect(mockFetchServerVersion).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  it("경로가 바뀌고 서버 버전이 같으면 리로드하지 않는다", async () => {
    mockFetchServerVersion.mockResolvedValue(__APP_VERSION__);
    const { rerender } = renderHook(() => useVersionReload());
    routerState.pathname = "/list";
    rerender();
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it("경로가 바뀌고 서버 버전이 다르면 리로드한다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    const { rerender } = renderHook(() => useVersionReload());
    routerState.pathname = "/list";
    rerender();
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("언마운트 후 도착한 응답은 리로드하지 않는다", async () => {
    let resolveVersion: (version: string) => void = () => {};
    mockFetchServerVersion.mockReturnValue(
      new Promise<string>((resolve) => {
        resolveVersion = resolve;
      }),
    );
    const { rerender, unmount } = renderHook(() => useVersionReload());
    routerState.pathname = "/list";
    rerender();
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );

    unmount();
    resolveVersion("0.0.0-different");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(reload).not.toHaveBeenCalled();
  });

  it("서버 버전 조회 실패는 화면 전환을 막지 않고 경고만 남긴다", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mockFetchServerVersion.mockRejectedValue(new Error("offline"));
    const { rerender } = renderHook(() => useVersionReload());
    routerState.pathname = "/list";
    rerender();
    await waitFor(() => expect(warn).toHaveBeenCalledTimes(1));
    warn.mockRestore();
    expect(reload).not.toHaveBeenCalled();
  });
});
