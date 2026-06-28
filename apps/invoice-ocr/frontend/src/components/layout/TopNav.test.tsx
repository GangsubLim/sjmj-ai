import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TopNav } from "./TopNav";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TopNav />
    </MemoryRouter>,
  );
}

describe("TopNav", () => {
  it("정확히 6개의 navItem을 렌더한다 (작성/목록/거래처/품목/실적/설정)", () => {
    renderAt("/");
    const labels = ["작성", "목록", "거래처", "품목", "실적", "설정"];
    for (const label of labels) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("/sales-performance 에서 '실적'이 활성화된다", () => {
    renderAt("/sales-performance");
    const link = screen.getByText("실적").closest("a");
    expect(link?.className ?? "").toMatch(/text-primary/);
  });

  it("/list 에서는 '작성'이 활성화되지 않는다 (end prop 회귀)", () => {
    renderAt("/list");
    const writeLink = screen.getByText("작성").closest("a");
    expect(writeLink?.className ?? "").not.toMatch(/text-primary after:/);
  });
});
