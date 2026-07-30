import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Link, Outlet } from "react-router-dom";
import { fetchServerVersion } from "@/services/api";
import { useVersionReload } from "./use-version-reload";

vi.mock("@/services/api", () => ({ fetchServerVersion: vi.fn() }));

const mockFetchServerVersion = vi.mocked(fetchServerVersion);

function ProbeLayout() {
  useVersionReload();
  return (
    <div>
      <Link to="/list">목록</Link>
      <Link to="/">홈</Link>
      <Outlet />
    </div>
  );
}

// main.tsx는 앱 전체를 <StrictMode>로 감싼다 — dev 빌드에서 effect가
// mount → cleanup → mount로 두 번 도는 실제 구성이므로 그대로 재현한다.
function renderApp({ strict = false }: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<ProbeLayout />}>
          <Route path="/" element={<p>홈 화면</p>} />
          <Route path="/list" element={<p>목록 화면</p>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

describe("useVersionReload — 실제 라우터", () => {
  let reload: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    mockFetchServerVersion.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("마운트 시엔 확인하지 않고, 실제 내비게이션 후 1회 확인한다", async () => {
    mockFetchServerVersion.mockResolvedValue(__APP_VERSION__);
    renderApp();
    expect(mockFetchServerVersion).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("link", { name: "목록" }));
    expect(screen.getByText("목록 화면")).toBeInTheDocument();
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it("버전이 다르면 실제 내비게이션 후 리로드한다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    renderApp();
    fireEvent.click(screen.getByRole("link", { name: "목록" }));
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
    fireEvent.click(screen.getByRole("link", { name: "목록" }));
    await waitFor(() =>
      expect(mockFetchServerVersion).toHaveBeenCalledTimes(1),
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it("StrictMode에서도 버전이 다르면 내비게이션 후 리로드한다", async () => {
    mockFetchServerVersion.mockResolvedValue("0.0.0-different");
    renderApp({ strict: true });
    fireEvent.click(screen.getByRole("link", { name: "목록" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(mockFetchServerVersion).toHaveBeenCalledTimes(1);
  });
});
