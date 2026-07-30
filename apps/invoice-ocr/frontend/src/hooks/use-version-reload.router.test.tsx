import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { fetchServerVersion } from "@/services/api";

vi.mock("@/services/api", () => ({ fetchServerVersion: vi.fn() }));

const mockFetchServerVersion = vi.mocked(fetchServerVersion);

// 훅을 흉내 낸 probe 레이아웃이 아니라 실제 AppShell을 렌더한다 — AppShell의
// useVersionReload() 호출이 사라지면(리팩터 사고) 이 파일이 RED가 돼야 한다.
// 라우트 구성은 main.tsx와 같은 layout-route 형태이고, 내비게이션은 AppShell이
// 실제로 그리는 BottomNav 링크로 일으킨다(matchMedia matches:false → 모바일).
// main.tsx는 앱 전체를 <StrictMode>로 감싼다 — dev 빌드에서 effect가
// mount → cleanup → mount로 두 번 도는 실제 구성이므로 그대로 재현한다.
function renderApp({ strict = false }: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<p>홈 화면</p>} />
          <Route path="/list" element={<p>목록 화면</p>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

describe("useVersionReload — 실제 라우터 + AppShell 배선", () => {
  let reload: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    mockFetchServerVersion.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("마운트 시엔 확인하지 않고, 실제 내비게이션 후 1회 확인한다", async () => {
    mockFetchServerVersion.mockResolvedValue(__APP_VERSION__);
    renderApp();
    expect(mockFetchServerVersion).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("link", { name: "명세서" }));
    expect(screen.getByText("목록 화면")).toBeInTheDocument();
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it("버전이 다르면 실제 내비게이션 후 리로드한다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    renderApp();
    fireEvent.click(screen.getByRole("link", { name: "명세서" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("StrictMode 이중 마운트에서도 최초 마운트에서는 확인하지 않는다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    renderApp({ strict: true });
    expect(screen.getByText("홈 화면")).toBeInTheDocument();
    await waitFor(() => expect(mockFetchServerVersion).not.toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
  });

  it("StrictMode에서 내비게이션 후 확인은 1회뿐이다", async () => {
    mockFetchServerVersion.mockResolvedValue(__APP_VERSION__);
    renderApp({ strict: true });
    fireEvent.click(screen.getByRole("link", { name: "명세서" }));
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it("StrictMode에서도 버전이 다르면 내비게이션 후 리로드한다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    renderApp({ strict: true });
    fireEvent.click(screen.getByRole("link", { name: "명세서" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(mockFetchServerVersion).toHaveBeenCalledTimes(1);
  });
});
