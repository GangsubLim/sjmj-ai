import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchServerVersion } from "./api";

function mockHealth(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("fetchServerVersion", () => {
  it("상대 경로 /api/health를 no-store로 호출하고 version을 반환한다", async () => {
    const fetchSpy = mockHealth({ status: "ok", version: "0.5.0" });
    await expect(fetchServerVersion()).resolves.toBe("0.5.0");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({
        cache: "no-store",
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("getApiBaseUrl()을 경유하지 않는다 — VITE_API_URL이 설정돼도 상대 경로를 쓴다", async () => {
    // 근거: base URL 폴백은 레거시 PHP(kslim) 또는 localhost:8000을 가리킬 수 있다.
    // 버전 확인은 dist를 서빙한 오리진에 물어야만 의미가 있으므로 상대 경로가 계약이다.
    // env가 설정된 상태에서도 계약이 유지되는지 확인해, 훗날 getApiBaseUrl() 경유로
    // "고치는" 회귀를 잡는다.
    vi.stubEnv("VITE_API_URL", "https://other.example/api");
    const fetchSpy = mockHealth({ status: "ok", version: "0.5.0" });
    await fetchServerVersion();
    const [url] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/health");
    expect(String(url)).not.toMatch(/^https?:\/\//);
  });

  it("2xx가 아니면 throw한다", async () => {
    mockHealth({}, 503);
    await expect(fetchServerVersion()).rejects.toThrow(/503/);
  });

  it("version 필드가 문자열이 아니면 throw한다", async () => {
    mockHealth({ status: "ok" });
    await expect(fetchServerVersion()).rejects.toThrow(/version/);
  });

  it("응답 바디가 null이어도 version 누락과 같은 메시지로 throw한다", async () => {
    mockHealth(null);
    await expect(fetchServerVersion()).rejects.toThrow(/version 문자열이 없다/);
  });
});
