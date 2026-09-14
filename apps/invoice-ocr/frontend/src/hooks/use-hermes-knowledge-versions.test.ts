import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useHermesKnowledgeVersions } from "./use-hermes-knowledge-versions";
import { hermesAPI } from "@/services/api";
import type { HermesKnowledgeVersion } from "@/types/hermes";

vi.mock("@/services/api", () => ({
  hermesAPI: { getKnowledgeVersions: vi.fn() },
}));

const mockGet = vi.mocked(hermesAPI.getKnowledgeVersions);

const ROW: HermesKnowledgeVersion = {
  version: 2,
  published_at: "2026-09-08T03:01:14",
  corrections_through: 25,
  rejected: null,
  changes: [],
  missing_file: false,
};

describe("useHermesKnowledgeVersions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("버전 목록을 서버 순서 그대로 노출한다", async () => {
    mockGet.mockResolvedValue({
      success: true,
      data: [ROW, { ...ROW, version: 1 }],
    });
    const { result } = renderHook(() => useHermesKnowledgeVersions());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.versions.map((v) => v.version)).toEqual([2, 1]);
    expect(result.current.error).toBeNull();
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("실패하면 error만 채우고 목록은 비워 둔다", async () => {
    mockGet.mockRejectedValue(new Error("knowledge down"));
    const { result } = renderHook(() => useHermesKnowledgeVersions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.versions).toEqual([]);
    expect(result.current.error).toBe("knowledge down");
  });
});
