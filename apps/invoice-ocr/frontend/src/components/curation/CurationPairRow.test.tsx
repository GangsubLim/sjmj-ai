import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";

import { CurationPairRow } from "./CurationPairRow";
import type { CurationJobPair } from "@/types/curation";

vi.mock("@/hooks/use-items", () => ({ useItems: () => ({ data: [] }) }));

// cmdk/radix-popover가 jsdom에서 마운트되려면 아래 브라우저 API가 필요하다.
beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
  }
});

function pairWith(
  canonical: string,
  over: Partial<CurationJobPair> = {},
): CurationJobPair {
  return {
    id: 9001,
    crop_ref: "1/0",
    row_index: 0,
    draft_label: "무우",
    final_label: "무",
    canonical_label: canonical,
    supply: 8000,
    status: "included",
    reviewed_at: null,
    uncertain: false,
    top5: [
      { label: "무", sim: 0.77 },
      { label: "배추", sim: 0.42 },
    ],
    ...over,
  };
}

describe("CurationPairRow", () => {
  it("pair.canonical_label 외부 갱신 시 입력 표시값이 따라간다(서버 merge 재동기)", () => {
    const { rerender } = render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    const input = screen.getByLabelText("행 0 라벨") as HTMLInputElement;
    expect(input.value).toBe("무");

    // 서버 정규화 결과가 merge된 것처럼 새 canonical_label로 rerender.
    rerender(
      <CurationPairRow jobId={1} pair={pairWith("무우")} onPatch={vi.fn()} />,
    );
    expect(input.value).toBe("무우");
  });

  it("라벨 blur 시 변경된 canonical_label로 onPatch를 호출한다", () => {
    const onPatch = vi.fn();
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={onPatch} />,
    );
    const input = screen.getByLabelText("행 0 라벨");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "배추" } });
    fireEvent.blur(input);

    expect(onPatch).toHaveBeenCalledWith(9001, { canonical_label: "배추" });
  });

  it("top5를 클릭 가능한 칩으로 보여준다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /배추/ })).toBeInTheDocument();
  });

  it("칩 클릭 시 canonical_label PATCH를 요청한다", () => {
    const onPatch = vi.fn();
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={onPatch} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /배추/ }));
    expect(onPatch).toHaveBeenCalledWith(9001, { canonical_label: "배추" });
  });

  it("미확신 행에 배지를 표시한다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", { uncertain: true })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/미확신/)).toBeInTheDocument();
  });

  it("확신 행에는 배지가 없다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    expect(screen.queryByText(/미확신/)).not.toBeInTheDocument();
  });

  it("후보가 없으면 안내 문구를 보여준다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", { top5: [] })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText("후보 없음")).toBeInTheDocument();
  });

  it("직접 입력(Autocomplete)은 그대로 유지된다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    expect(screen.getByLabelText("행 0 라벨")).toBeInTheDocument();
  });
});
