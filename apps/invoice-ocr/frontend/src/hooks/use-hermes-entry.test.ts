import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useHermesEntry } from "./use-hermes-entry";
import { hermesAPI } from "@/services/api";
import type { HermesEntryDetail } from "@/types/hermes";

vi.mock("@/services/api", () => ({ hermesAPI: { getEntry: vi.fn() } }));
const mockGetEntry = vi.mocked(hermesAPI.getEntry);

const DETAIL: HermesEntryDetail = {
  id: 573,
  status: "mismatch",
  draft: { issue_date: "2026-09-05", recipient: "○○상사", grand_total: 165000 },
  final: {
    id: 573,
    issue_date: "2026-09-03",
    recipient: "○○상사",
    grand_total: 165000,
  },
  rows: [
    {
      index: 0,
      raw: { text: "히타", conf: "중" },
      draft: {
        name: "히타",
        quantity: 1,
        unit: "EA",
        unit_price: 150000,
        supply: 150000,
        deduction: false,
      },
      final: {
        name: "히터",
        quantity: 1,
        unit: "EA",
        unit_price: 150000,
        supply: 150000,
        deduction: false,
      },
      mismatch: ["name"],
    },
  ],
  has_photo: true,
};

describe("useHermesEntry", () => {
  beforeEach(() => vi.clearAllMocks());

  it("상세를 불러온다", async () => {
    mockGetEntry.mockResolvedValue({ data: DETAIL });
    const { result } = renderHook(() => useHermesEntry(573));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.entry?.rows[0].mismatch).toEqual(["name"]);
    expect(mockGetEntry).toHaveBeenCalledWith(573);
  });

  it("id가 없으면 요청하지 않는다", async () => {
    const { result } = renderHook(() => useHermesEntry(undefined));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockGetEntry).not.toHaveBeenCalled();
    expect(result.current.entry).toBeNull();
  });

  it("실패 메시지를 노출한다", async () => {
    mockGetEntry.mockRejectedValue(new Error("없는 건"));
    const { result } = renderHook(() => useHermesEntry(1));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("없는 건");
  });

  it("유효 id에서 undefined로 바뀌면 옛 값이 남지 않는다", async () => {
    // 반환값을 렌더 시 파생하므로 effect의 setState 없이도 즉시 접힌다
    // (react-hooks/set-state-in-effect 회피 + 진행 중 요청은 reqId로 무효화,
    // use-job-neighbors.ts와 동일 관례).
    mockGetEntry.mockResolvedValue({ data: DETAIL });
    const { result, rerender } = renderHook(
      ({ id }: { id: number | undefined }) => useHermesEntry(id),
      { initialProps: { id: 573 as number | undefined } },
    );
    await waitFor(() => expect(result.current.entry?.id).toBe(573));

    rerender({ id: undefined });

    expect(result.current.entry).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("조회 도중 id가 사라지면 loading이 고착되지 않는다", async () => {
    // resolve되지 않는 프라미스로 "응답이 아직 안 왔다"를 재현한다.
    mockGetEntry.mockReturnValue(new Promise(() => {}));
    const { result, rerender } = renderHook(
      ({ id }: { id: number | undefined }) => useHermesEntry(id),
      { initialProps: { id: 573 as number | undefined } },
    );
    await waitFor(() => expect(result.current.loading).toBe(true));

    rerender({ id: undefined });

    expect(result.current.loading).toBe(false);
  });

  it("역순 도착한 옛 요청이 최신 엔트리를 덮지 않는다", async () => {
    // 먼저 시작한 요청(id=1)이 나중에 끝나고, 나중에 시작한 요청(id=2)이 먼저
    // 끝나는 역전 상황을 재현한다. reqId 소유권 가드가 없으면 옛 요청의 응답이
    // 최신 엔트리를 덮는다(use-job-neighbors.test.ts의 동명 테스트와 같은 취지).
    let resolveFirst!: (v: { data: HermesEntryDetail }) => void;
    let resolveSecond!: (v: { data: HermesEntryDetail }) => void;
    mockGetEntry
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve)),
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveSecond = resolve)),
      );

    const { result, rerender } = renderHook(
      ({ id }: { id: number | undefined }) => useHermesEntry(id),
      { initialProps: { id: 1 } },
    );
    await waitFor(() => expect(mockGetEntry).toHaveBeenCalledWith(1));

    rerender({ id: 2 });
    await waitFor(() => expect(mockGetEntry).toHaveBeenCalledWith(2));

    // 나중에 시작한 요청(id=2)을 먼저 resolve한다.
    resolveSecond({ data: { ...DETAIL, id: 2 } });
    await waitFor(() => expect(result.current.entry?.id).toBe(2));

    // 먼저 시작한 요청(id=1)을 뒤늦게 resolve한다 — 옛 엔트리를 들고 있다.
    // 실측 결과 setTimeout(0)은 act() 밖 setState의 React 스케줄러 플러시보다 먼저
    // 끝나 가드가 없어도 우연히 통과했다 — 50ms로 여유를 둬 실제로 덮어쓰기를
    // 잡아내는지 확인했다(가드 제거 시 FAIL, 복원 시 PASS를 fix report에 남김).
    resolveFirst({ data: { ...DETAIL, id: 1 } });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.entry?.id).toBe(2);
  });
});
