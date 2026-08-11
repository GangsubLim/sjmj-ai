import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ComponentProps } from "react";

import { JobNavButtons } from "./JobNavButtons";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function setup(over: Partial<ComponentProps<typeof JobNavButtons>> = {}) {
  return render(
    <MemoryRouter>
      <JobNavButtons
        basePath="/curation"
        page={3}
        prev={null}
        next={null}
        loading={false}
        {...over}
      />
    </MemoryRouter>,
  );
}

describe("JobNavButtons", () => {
  beforeEach(() => vi.clearAllMocks());

  it("이웃이 없으면 이전/다음이 비활성이다", () => {
    setup();
    expect(screen.getByRole("button", { name: "← 이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeDisabled();
  });

  it("조회 중이면 이웃이 있어도 이전/다음이 비활성이다", () => {
    setup({
      prev: { jobId: 6, page: 2 },
      next: { jobId: 8, page: 3 },
      loading: true,
    });
    expect(screen.getByRole("button", { name: "← 이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 →" })).toBeDisabled();
  });

  it("목록 버튼은 현재 page를 유지한 목록 URL로 push 이동한다", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));
    expect(mockNavigate).toHaveBeenCalledWith("/curation?page=3");
  });

  it("이전 버튼은 그 이웃이 속한 페이지를 붙여 replace 이동한다", () => {
    setup({ prev: { jobId: 6, page: 2 } });
    fireEvent.click(screen.getByRole("button", { name: "← 이전" }));
    expect(mockNavigate).toHaveBeenCalledWith("/curation/6?page=2", {
      replace: true,
    });
  });

  it("다음 버튼은 확정 전 basePath에서도 같은 규칙으로 replace 이동한다", () => {
    setup({
      basePath: "/curation/pending",
      next: { jobId: 21, page: 1 },
    });
    fireEvent.click(screen.getByRole("button", { name: "다음 →" }));
    expect(mockNavigate).toHaveBeenCalledWith("/curation/pending/21", {
      replace: true,
    });
  });
});
