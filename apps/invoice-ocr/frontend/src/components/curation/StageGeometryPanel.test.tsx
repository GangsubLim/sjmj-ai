import { describe, it, expect, vi, beforeEach } from "vitest";
// fireEvent를 쓴다 — @testing-library/user-event는 이 레포의 의존이 아니다(package.json 미포함).
// 기존 선례: src/components/curation/CurationPairRow.test.tsx:1.
import { render, screen, fireEvent } from "@testing-library/react";

import { StageGeometryPanel } from "@/components/curation/StageGeometryPanel";
import { useJobGeometry } from "@/hooks/use-job-geometry";
import type { StageGeometry } from "@/types/curation";

vi.mock("@/hooks/use-job-geometry", () => ({ useJobGeometry: vi.fn() }));

const PARTIAL: StageGeometry = {
  version: 1,
  generation: 0,
  image_size: [3024, 4032],
  warp_size: [900, 2100],
  quad: [
    [10, 20],
    [30, 20],
    [30, 40],
    [10, 40],
  ],
  quad_source: "color",
  deskew_deg: 0.42,
};

const FULL: StageGeometry = {
  ...PARTIAL,
  hlines: [614, 696],
  pitch: 82,
  item_x: [96, 396],
  amount_x: [630, 896],
  rows: [
    { band: [612, 694], type: "new", item_box: [618, 690], row_index: 0 },
    { band: [694, 776], type: "cont", item_box: null, row_index: null },
  ],
};

const mocked = vi.mocked(useJobGeometry);

beforeEach(() => {
  mocked.mockReset();
});

describe("StageGeometryPanel", () => {
  it("파일 부재(404)면 아무것도 렌더하지 않는다 — 상위의 원본·워프 2장 폴백만 남는다", () => {
    mocked.mockReturnValue({ status: "absent" });

    const { container } = render(<StageGeometryPanel jobId={1} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("손상(500)은 눈에 보이게 닫는다 — 404 폴백으로 위장하지 않는다", () => {
    mocked.mockReturnValue({ status: "corrupt" });

    render(<StageGeometryPanel jobId={1} />);

    expect(screen.getByText(/기하 파일 손상/)).toBeInTheDocument();
  });

  it("이전 세대(409)는 그 사실을 알리고 기하를 그리지 않는다", () => {
    mocked.mockReturnValue({ status: "stale" });

    render(<StageGeometryPanel jobId={1} />);

    expect(screen.getByText(/이전 세대 기하/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /쿼드/ })).not.toBeInTheDocument();
  });

  it("모르는 version은 안내 문구로 닫는다", () => {
    mocked.mockReturnValue({
      status: "ready",
      geometry: { ...FULL, version: 99 },
    });

    render(<StageGeometryPanel jobId={1} />);

    expect(screen.getByText(/모르는 기하 형식/)).toBeInTheDocument();
  });

  it("강등 잡의 부분 문서는 상류 패널만 열고 하류 패널은 아예 없다", () => {
    mocked.mockReturnValue({ status: "ready", geometry: PARTIAL });

    render(<StageGeometryPanel jobId={1} />);

    expect(
      screen.getByRole("checkbox", { name: "② 쿼드" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "⑥ 행 분류" }),
    ).not.toBeInTheDocument();
  });

  it("워프 좌표계 viewBox는 상수가 아니라 파일의 warp_size에서 온다", () => {
    mocked.mockReturnValue({
      status: "ready",
      geometry: { ...FULL, warp_size: [800, 1800] },
    });

    render(<StageGeometryPanel jobId={1} />);

    expect(screen.getByTestId("overlay-rowClass")).toHaveAttribute(
      "viewBox",
      "0 0 800 1800",
    );
  });

  it("쿼드 오버레이 viewBox는 원본 좌표계(image_size)다", () => {
    mocked.mockReturnValue({ status: "ready", geometry: FULL });

    render(<StageGeometryPanel jobId={1} />);

    expect(screen.getByTestId("overlay-quad")).toHaveAttribute(
      "viewBox",
      "0 0 3024 4032",
    );
  });

  it("토글을 끄면 그 패널의 오버레이가 사라진다", () => {
    mocked.mockReturnValue({ status: "ready", geometry: FULL });
    render(<StageGeometryPanel jobId={1} />);

    fireEvent.click(screen.getByRole("checkbox", { name: "⑥ 행 분류" }));

    expect(screen.queryByTestId("overlay-rowClass")).not.toBeInTheDocument();
  });
});
