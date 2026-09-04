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

  it("조회 중이면 nav에 aria-busy를 세워 비활성 이유를 알린다", () => {
    // nav는 상세의 로딩·에러 분기에서도 마운트된 채 남는다 — "조회 중"과 "이웃 없음"을
    // 보조기술이 구분할 수 있어야 한다.
    const { rerender } = setup({ loading: true });
    expect(screen.getByRole("navigation", { name: "잡 이동" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    rerender(
      <MemoryRouter>
        <JobNavButtons
          basePath="/curation"
          page={3}
          prev={null}
          next={null}
          loading={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("navigation", { name: "잡 이동" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
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

  it("필터가 켜져 있으면 목록·이웃 이동 URL이 필터를 보존한다", () => {
    setup({ page: 2, prev: { jobId: 11, page: 2 }, rowDelta: true });

    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));
    expect(mockNavigate).toHaveBeenCalledWith(
      "/curation?page=2&row_delta=true",
    );

    fireEvent.click(screen.getByRole("button", { name: "← 이전" }));
    expect(mockNavigate).toHaveBeenCalledWith(
      "/curation/11?page=2&row_delta=true",
      { replace: true },
    );
  });

  it("필터를 넘기지 않은 확정 전 호출부는 URL이 도입 이전과 같다", () => {
    setup({
      basePath: "/curation/pending",
      page: 2,
      prev: { jobId: 11, page: 2 },
    });

    fireEvent.click(screen.getByRole("button", { name: "← 목록" }));
    expect(mockNavigate).toHaveBeenCalledWith("/curation/pending?page=2");
  });
});
