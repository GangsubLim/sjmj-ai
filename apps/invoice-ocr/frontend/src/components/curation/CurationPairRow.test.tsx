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
    exclusion_reason: null,
    reviewed_at: null,
    uncertain: false,
    crop_available: true,
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
    expect(
      screen.getByRole("button", { name: "후보 배추, 유사도 0.42" }),
    ).toBeInTheDocument();
  });

  it("칩 클릭 시 canonical_label PATCH를 요청한다", () => {
    const onPatch = vi.fn();
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={onPatch} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "후보 배추, 유사도 0.42" }),
    );
    expect(onPatch).toHaveBeenCalledWith(9001, { canonical_label: "배추" });
  });

  it("이미 선택된 라벨의 칩을 클릭해도 PATCH를 요청하지 않는다", () => {
    const onPatch = vi.fn();
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={onPatch} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "후보 무, 유사도 0.77" }),
    );
    expect(onPatch).not.toHaveBeenCalled();
  });

  it("현재 라벨과 같은 칩만 선택 상태(aria-pressed)로 노출한다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: "후보 무, 유사도 0.77" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: "후보 배추, 유사도 0.42" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  // mousedown(칩)→blur(입력)→click(칩) 순서 때문에, 막지 않으면 입력 중이던 텍스트가
  // 칩 선택보다 먼저 PATCH로 나가 같은 pair에 요청 2건이 겹친다.
  it("칩 mousedown은 기본동작을 막아 입력창 blur 커밋이 먼저 나가지 않게 한다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );
    const notCanceled = fireEvent.mouseDown(
      screen.getByRole("button", { name: "후보 배추, 유사도 0.42" }),
    );
    expect(notCanceled).toBe(false); // preventDefault 호출됨
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

  it("자동 배제된 행에 배지를 표시한다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          status: "excluded",
          exclusion_reason: "blank_crop",
        })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/빈 크롭/)).toBeInTheDocument();
  });

  it("사람이 배제한 행(사유 없음)에는 자동 배제 배지가 없다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", { status: "excluded", exclusion_reason: null })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByText(/빈 크롭/)).not.toBeInTheDocument();
  });

  it("기계가 배제했으나 사람이 되돌린 행에도 배지가 남는다", () => {
    // §6 세 번째 칸 — 사유가 남아 있는 included. 사람이 오탐을 식별할 수 있어야 한다.
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          status: "included",
          exclusion_reason: "blank_crop",
        })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/빈 크롭/)).toBeInTheDocument();
  });

  it("프론트가 모르는 사유에도 일반 문구로 배지를 표시한다", () => {
    // ADR 0006이 사유 축 확장을 예고한다 — 배지 표시 축은 "사유 != null"이므로
    // 유니온에 아직 없는 값이 와도 조용히 사라지지 않아야 한다.
    // 미래 사유를 재현하려면 유니온 밖 값이 필요해 필드 타입으로 좁히는 단언을 쓴다.
    const futureReason = String(
      "faint_on",
    ) as CurationJobPair["exclusion_reason"];
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          status: "excluded",
          exclusion_reason: futureReason,
        })}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/자동 배제/)).toBeInTheDocument();
  });

  it("되돌린 행과 배제된 행의 배지 문구가 서로 구분된다", () => {
    // 되돌림 칸은 현재 included인데 "자동 배제"라고만 적히면 행의 "제외" 버튼과 어긋난다.
    const badgeTextOf = (over: Partial<CurationJobPair>) => {
      const { unmount } = render(
        <CurationPairRow
          jobId={1}
          pair={pairWith("무", over)}
          onPatch={vi.fn()}
        />,
      );
      const text = screen.getByText(/자동 배제/).textContent ?? "";
      unmount();
      return text;
    };

    const excludedBadge = badgeTextOf({
      status: "excluded",
      exclusion_reason: "blank_crop",
    });
    const revertedBadge = badgeTextOf({
      status: "included",
      exclusion_reason: "blank_crop",
    });

    expect(excludedBadge).not.toMatch(/되돌림/);
    expect(revertedBadge).toMatch(/되돌림/);
    expect(revertedBadge).not.toBe(excludedBadge);
  });
});

describe("미결 쌍(승계 실패)", () => {
  it("crop_available이 false면 crop 이미지를 만들지 않는다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          crop_available: false,
          status: "excluded",
          exclusion_reason: "relink_failed",
        })}
        onPatch={vi.fn()}
      />,
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("그림 없음")).toBeInTheDocument();
  });

  it("승계 실패 배지를 사유 문구로 띄운다", () => {
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          crop_available: false,
          status: "excluded",
          exclusion_reason: "relink_failed",
        })}
        onPatch={vi.fn()}
      />,
    );

    expect(screen.getByText(/승계 실패/)).toBeInTheDocument();
  });

  it("사람이 배제를 토글해 사유가 지워져도 승계 실패 표식이 남는다", () => {
    // 백엔드는 사람 배제 시 exclusion_reason을 NULL로 지운다(ADR 0006 §6) — 배지를
    // 사유에 걸면 검수자가 한 번만 토글해도 문구가 영구 소실된다. 영속 표식은
    // crop_available === false다(curation_service._has_row_crop과 같은 축).
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", {
          crop_available: false,
          status: "excluded",
          exclusion_reason: null,
        })}
        onPatch={vi.fn()}
      />,
    );

    expect(screen.getByText(/승계 실패/)).toBeInTheDocument();
  });

  it("미결 쌍은 옛 세대의 행 번호를 그대로 노출하지 않는다", () => {
    // row_index는 이제 다른 줄을 가리킨다 — 그대로 보이면 검수자가 그 번호로 다른 줄의
    // 그림과 대조하게 된다(crop URL만 막아서는 부족하다).
    render(
      <CurationPairRow
        jobId={1}
        pair={pairWith("무", { crop_available: false, row_index: 3 })}
        onPatch={vi.fn()}
      />,
    );

    expect(screen.queryByText("#3")).toBeNull();
  });

  it("crop_available이 true면 crop 이미지를 그대로 그린다", () => {
    render(
      <CurationPairRow jobId={1} pair={pairWith("무")} onPatch={vi.fn()} />,
    );

    expect(screen.getByRole("img")).toBeInTheDocument();
  });
});
