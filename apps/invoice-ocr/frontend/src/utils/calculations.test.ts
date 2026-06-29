import {
  calculateSupplyAndVat,
  calculateItem,
  calculateTotals,
  numberToKorean,
  formatNumber,
} from "./calculations";
import type { InvoiceItem } from "@/types/invoice";

// --- calculateSupplyAndVat ---

describe("calculateSupplyAndVat", () => {
  it("기본 공급가액과 부가세를 계산한다", () => {
    const result = calculateSupplyAndVat(10000, 2);
    expect(result).toEqual({ supply: 20000, vat: 2000, total: 22000 });
  });

  it("단가가 0이면 모두 0이다", () => {
    expect(calculateSupplyAndVat(0, 5)).toEqual({
      supply: 0,
      vat: 0,
      total: 0,
    });
  });

  it("수량이 0이면 모두 0이다", () => {
    expect(calculateSupplyAndVat(5000, 0)).toEqual({
      supply: 0,
      vat: 0,
      total: 0,
    });
  });

  it("VAT를 반올림한다 (소수점 발생 시)", () => {
    // 333 * 1 = 333, VAT = 33.3 → 33
    const result = calculateSupplyAndVat(333, 1);
    expect(result.vat).toBe(33);
    expect(result.total).toBe(366);
  });

  it("큰 숫자를 정확히 계산한다", () => {
    const result = calculateSupplyAndVat(1500000, 10);
    expect(result).toEqual({
      supply: 15000000,
      vat: 1500000,
      total: 16500000,
    });
  });

  it("음수 단가를 처리한다", () => {
    const result = calculateSupplyAndVat(-1000, 2);
    expect(result.supply).toBe(-2000);
    expect(result.vat).toBe(-200);
    expect(result.total).toBe(-2200);
  });
});

// --- calculateItem ---

describe("calculateItem", () => {
  it("문자열 수량/단가를 숫자로 변환하여 계산한다", () => {
    const item = calculateItem({
      name: "엔진오일",
      quantity: "3",
      unit_price: "50000",
    });
    expect(item.supply).toBe(150000);
    expect(item.vat).toBe(15000);
    expect(item.total).toBe(165000);
  });

  it("빈 문자열은 0으로 처리한다", () => {
    const item = calculateItem({
      name: "테스트",
      quantity: "",
      unit_price: "",
    });
    expect(item.supply).toBe(0);
    expect(item.vat).toBe(0);
    expect(item.total).toBe(0);
  });

  it("공제 항목은 음수로 처리한다", () => {
    const item = calculateItem({
      name: "할인",
      quantity: 1,
      unit_price: 10000,
      deduction: true,
    });
    expect(item.supply).toBe(-10000);
    expect(item.vat).toBe(-1000);
    expect(item.total).toBe(-11000);
    expect(item.deduction).toBe(true);
  });

  it("원래 값(문자열)을 유지한다", () => {
    const item = calculateItem({
      name: "부품",
      quantity: "5",
      unit_price: "20000",
    });
    expect(item.quantity).toBe("5");
    expect(item.unit_price).toBe("20000");
  });

  it("name이 없으면 빈 문자열로 기본값 설정", () => {
    const item = calculateItem({ quantity: 1, unit_price: 1000 });
    expect(item.name).toBe("");
  });

  it("quantity/unit_price가 undefined이면 0으로 기본값 설정", () => {
    const item = calculateItem({ name: "테스트" });
    expect(item.quantity).toBe(0);
    expect(item.unit_price).toBe(0);
    expect(item.supply).toBe(0);
  });

  it("숫자가 아닌 문자열은 0으로 처리한다", () => {
    const item = calculateItem({
      name: "X",
      quantity: "abc",
      unit_price: "1000",
    });
    expect(item.supply).toBe(0);
  });

  it("소수점 문자열은 소수 부분을 포함하여 계산한다", () => {
    const item = calculateItem({
      name: "X",
      quantity: "2.9",
      unit_price: "1000",
    });
    // Number("2.9") = 2.9
    expect(item.supply).toBe(2900);
  });
});

// --- calculateTotals ---

describe("calculateTotals", () => {
  it("모든 항목의 합계를 계산한다", () => {
    const items: InvoiceItem[] = [
      {
        name: "A",
        quantity: 1,
        unit_price: 10000,
        supply: 10000,
        vat: 1000,
        total: 11000,
        item_order: 1,
        deduction: false,
      },
      {
        name: "B",
        quantity: 2,
        unit_price: 5000,
        supply: 10000,
        vat: 1000,
        total: 11000,
        item_order: 2,
        deduction: false,
      },
    ];
    expect(calculateTotals(items)).toEqual({
      total_supply: 20000,
      total_vat: 2000,
      grand_total: 22000,
    });
  });

  it("빈 배열이면 모두 0이다", () => {
    expect(calculateTotals([])).toEqual({
      total_supply: 0,
      total_vat: 0,
      grand_total: 0,
    });
  });

  it("공제 항목을 포함하여 합산한다", () => {
    const items: InvoiceItem[] = [
      {
        name: "정비",
        quantity: 1,
        unit_price: 100000,
        supply: 100000,
        vat: 10000,
        total: 110000,
        item_order: 1,
        deduction: false,
      },
      {
        name: "할인",
        quantity: 1,
        unit_price: 10000,
        supply: -10000,
        vat: -1000,
        total: -11000,
        item_order: 2,
        deduction: true,
      },
    ];
    expect(calculateTotals(items)).toEqual({
      total_supply: 90000,
      total_vat: 9000,
      grand_total: 99000,
    });
  });
});

// --- numberToKorean ---

describe("numberToKorean", () => {
  it("0은 '영'을 반환한다", () => {
    expect(numberToKorean(0)).toBe("영");
  });

  it("한 자리 숫자를 변환한다", () => {
    expect(numberToKorean(5)).toBe("오 원정");
  });

  it("십 단위를 변환한다 (일 생략)", () => {
    // 10 → 십, not 일십
    expect(numberToKorean(10)).toBe("십 원정");
    expect(numberToKorean(15)).toBe("십오 원정");
  });

  it("백 단위를 변환한다 (일 생략)", () => {
    expect(numberToKorean(100)).toBe("백 원정");
    expect(numberToKorean(350)).toBe("삼백오십 원정");
  });

  it("천 단위를 변환한다 (일 생략)", () => {
    expect(numberToKorean(1000)).toBe("천 원정");
    expect(numberToKorean(5500)).toBe("오천오백 원정");
  });

  it("만 단위를 변환한다 (일만 형태)", () => {
    expect(numberToKorean(10000)).toBe("일만 원정");
    expect(numberToKorean(50000)).toBe("오만 원정");
  });

  it("억 단위를 변환한다 (일억 형태)", () => {
    expect(numberToKorean(100000000)).toBe("일억 원정");
  });

  it("복합 숫자를 변환한다", () => {
    expect(numberToKorean(12345)).toBe("일만 이천삼백사십오 원정");
  });

  it("음수는 '마이너스'를 붙인다", () => {
    expect(numberToKorean(-5000)).toBe("마이너스 오천 원정");
  });

  it("실제 청구 금액을 올바르게 변환한다", () => {
    // 1,650,000원
    expect(numberToKorean(1650000)).toBe("백육십오만 원정");
  });
});

// --- formatNumber ---

describe("formatNumber", () => {
  it("천단위 콤마를 추가한다", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("1000 미만은 콤마 없이 반환한다", () => {
    expect(formatNumber(999)).toBe("999");
  });

  it("0을 올바르게 반환한다", () => {
    expect(formatNumber(0)).toBe("0");
  });

  it("음수에도 콤마를 적용한다", () => {
    expect(formatNumber(-1234567)).toBe("-1,234,567");
  });
});
