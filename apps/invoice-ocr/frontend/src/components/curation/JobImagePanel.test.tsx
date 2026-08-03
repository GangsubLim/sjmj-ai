import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { JobImagePanel } from "./JobImagePanel";

describe("JobImagePanel", () => {
  it("원본과 워프 이미지를 항상 렌더한다", () => {
    render(<JobImagePanel jobId={42} />);

    const original = screen.getByAltText("원본 전표");
    const warped = screen.getByAltText("워프 전표");
    expect(original.getAttribute("src")).toContain(
      "/curation/jobs/42/image/original",
    );
    // 워프 산출이 없을 수도 있지만 분기하지 않는다 — 항상 시도하고 404면 폴백한다.
    expect(warped.getAttribute("src")).toContain(
      "/curation/jobs/42/image/warped",
    );
  });

  it("이미지 로드 실패 시 placeholder로 폴백한다", () => {
    // fireEvent.error는 기존 선례와 같다(app/curation/[jobId]/page.test.tsx:77).
    // img의 error는 원래 버블링하지 않아 수동 dispatch는 React 위임에 안 걸릴 수 있다.
    render(<JobImagePanel jobId={7} />);
    const warped = screen.getByAltText("워프 전표") as HTMLImageElement;

    fireEvent.error(warped);

    expect(warped.src).toContain("data:image/svg+xml");
  });
});
