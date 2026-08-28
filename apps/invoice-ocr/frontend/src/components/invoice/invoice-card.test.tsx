import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { InvoiceCard } from "./invoice-card";
import type { Invoice } from "@/types/invoice";

function invoice(over: Partial<Invoice> = {}): Invoice {
  return {
    id: 341,
    document_title: "거 래 명 세 서",
    issue_date: "2026-05-15",
    recipient: "한양운수",
    vehicle_no: "12가3456",
    show_stamp: true,
    total_supply: 100000,
    total_vat: 10000,
    grand_total: 110000,
    items: [],
    ...over,
  };
}

function setup(over: Partial<Invoice> = {}) {
  return render(
    <MemoryRouter>
      <InvoiceCard invoice={invoice(over)} />
    </MemoryRouter>,
  );
}

describe("InvoiceCard", () => {
  it("OCR 유래 명세서는 잡 번호 배지를 보여준다", () => {
    setup({ ocr_job_id: 128 });
    expect(screen.getByText("잡 #128")).toBeInTheDocument();
  });

  it("수기 입력 명세서는 잡 번호 배지를 그리지 않는다", () => {
    setup({ ocr_job_id: null });
    expect(screen.queryByText(/^잡 #/)).not.toBeInTheDocument();
  });
});
