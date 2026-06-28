import {
  formatPrice,
  formatBusinessNumber,
  formatPhoneNumber,
} from "./formatters";

// --- formatPrice ---

describe("formatPrice", () => {
  it("천단위 콤마를 추가한다", () => {
    expect(formatPrice(1500000)).toBe("1,500,000");
  });

  it("0을 반환한다", () => {
    expect(formatPrice(0)).toBe("0");
  });

  it("음수에도 콤마를 적용한다", () => {
    expect(formatPrice(-12345)).toBe("-12,345");
  });

  it("1000 미만은 콤마 없이 반환한다", () => {
    expect(formatPrice(500)).toBe("500");
  });
});

// --- formatBusinessNumber ---

describe("formatBusinessNumber", () => {
  it("10자리를 XXX-XX-XXXXX로 포맷한다", () => {
    expect(formatBusinessNumber("1234567890")).toBe("123-45-67890");
  });

  it("3자리 이하는 그대로 반환한다", () => {
    expect(formatBusinessNumber("123")).toBe("123");
  });

  it("4~5자리는 XXX-XX 형태로 반환한다", () => {
    expect(formatBusinessNumber("12345")).toBe("123-45");
  });

  it("비숫자 문자를 제거한다", () => {
    expect(formatBusinessNumber("123-45-67890")).toBe("123-45-67890");
  });

  it("10자리를 초과하면 잘라낸다", () => {
    expect(formatBusinessNumber("12345678901234")).toBe("123-45-67890");
  });

  it("빈 문자열을 처리한다", () => {
    expect(formatBusinessNumber("")).toBe("");
  });
});

// --- formatPhoneNumber ---

describe("formatPhoneNumber", () => {
  it("02 지역번호 (9자리)를 포맷한다", () => {
    expect(formatPhoneNumber("0212345678")).toBe("02-1234-5678");
  });

  it("02 지역번호 (짧은 형태)를 포맷한다", () => {
    expect(formatPhoneNumber("021234567")).toBe("02-123-4567");
  });

  it("일반 지역번호를 포맷한다", () => {
    expect(formatPhoneNumber("0311234567")).toBe("031-123-4567");
  });

  it("휴대폰 번호를 포맷한다", () => {
    expect(formatPhoneNumber("01012345678")).toBe("010-1234-5678");
  });

  it("비숫자 문자를 제거한다", () => {
    expect(formatPhoneNumber("010-1234-5678")).toBe("010-1234-5678");
  });

  it("3자리 이하는 그대로 반환한다", () => {
    expect(formatPhoneNumber("010")).toBe("010");
  });

  it("11자리를 초과하는 번호를 잘라낸다", () => {
    expect(formatPhoneNumber("010123456789999")).toBe("010-1234-5678");
  });
});
