import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { ItemOcrCandidates } from "./item-ocr-candidates";
import type { OcrItemMeta } from "./ocr-prefill";

vi.mock("@/services/api", () => ({
  ocrCropUrl: (jobId: number, row: number) =>
    `/api/ocr/jobs/${jobId}/crop/${row}`,
}));

function meta(over: Partial<OcrItemMeta> = {}): OcrItemMeta {
  return {
    candidates: [
      { label: "타이어", sim: 0.72 },
      { label: "튜브", sim: 0.68 },
      { label: "배터리", sim: 0.61 },
    ],
    uncertain: false,
    jobId: 7,
    rowIndex: 2,
    ...over,
  };
}

describe("ItemOcrCandidates", () => {
  it("모든 행에 지연 로딩 크롭 썸네일을 보여준다", () => {
    render(<ItemOcrCandidates meta={meta()} onPick={vi.fn()} />);
    const img = screen.getByRole("img", { name: /행 2/ });
    expect(img).toHaveAttribute("src", "/api/ocr/jobs/7/crop/2");
    expect(img).toHaveAttribute("loading", "lazy");
  });

  it("미확신 행은 배지와 함께 후보 칩이 기본 펼쳐진다", () => {
    render(
      <ItemOcrCandidates meta={meta({ uncertain: true })} onPick={vi.fn()} />,
    );
    expect(screen.getByText(/미확신/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /타이어/ })).toBeInTheDocument();
  });

  it("확신 행은 접혀 있고 '후보' 버튼으로 펼친다", () => {
    render(<ItemOcrCandidates meta={meta()} onPick={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /타이어/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /후보/ }));
    expect(screen.getByRole("button", { name: /타이어/ })).toBeInTheDocument();
  });

  it("토글 버튼은 펼침 상태와 무관하게 항상 마운트되고 aria-expanded로 상태를 전달한다", () => {
    render(<ItemOcrCandidates meta={meta()} onPick={vi.fn()} />);
    const toggle = screen.getByRole("button", { name: /후보/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /타이어/ })).toBeInTheDocument();
  });

  it("펼친 뒤 토글 버튼을 다시 누르면 후보 칩이 접힌다", () => {
    render(<ItemOcrCandidates meta={meta()} onPick={vi.fn()} />);
    const toggle = screen.getByRole("button", { name: /후보/ });

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: /타이어/ })).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(
      screen.queryByRole("button", { name: /타이어/ }),
    ).not.toBeInTheDocument();
  });

  it("칩 클릭 시 라벨과 0-based rank를 함께 넘긴다", () => {
    const onPick = vi.fn();
    render(
      <ItemOcrCandidates meta={meta({ uncertain: true })} onPick={onPick} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /배터리/ }));
    expect(onPick).toHaveBeenCalledWith("배터리", 2);
  });

  it("칩에 유사도를 함께 표시한다", () => {
    render(
      <ItemOcrCandidates meta={meta({ uncertain: true })} onPick={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /타이어/ })).toHaveTextContent(
      "0.72",
    );
  });

  it("칩의 접근 가능한 이름에 후보와 유사도 의미를 명시한다", () => {
    render(
      <ItemOcrCandidates meta={meta({ uncertain: true })} onPick={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: "후보 타이어, 유사도 0.72" }),
    ).toBeInTheDocument();
  });

  it("후보가 0개면 칩 대신 직접 입력 안내를 보여준다", () => {
    render(
      <ItemOcrCandidates
        meta={meta({ uncertain: true, candidates: [] })}
        onPick={vi.fn()}
      />,
    );
    expect(screen.getByText(/직접 입력/)).toBeInTheDocument();
  });
});
