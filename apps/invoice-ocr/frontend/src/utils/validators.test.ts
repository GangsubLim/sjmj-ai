import {
  validateBusinessNumber,
  validateRequired,
  validateFileSize,
} from "./validators";

// --- validateBusinessNumber ---

describe("validateBusinessNumber", () => {
  it("10자리 숫자는 유효하다", () => {
    expect(validateBusinessNumber("1234567890")).toEqual({ valid: true });
  });

  it("빈 문자열은 유효하다 (선택 필드)", () => {
    expect(validateBusinessNumber("")).toEqual({ valid: true });
  });

  it("10자리가 아니면 에러 메시지를 반환한다", () => {
    const result = validateBusinessNumber("12345");
    expect(result.valid).toBe(false);
    expect(result.message).toBe("사업자번호는 10자리 숫자여야 합니다");
  });

  it("하이픈이 포함된 문자열에서 숫자만 추출하여 검증한다", () => {
    expect(validateBusinessNumber("123-45-67890")).toEqual({ valid: true });
  });

  it("숫자가 아닌 문자가 섞여도 숫자만 추출한다", () => {
    const result = validateBusinessNumber("abc12345");
    expect(result.valid).toBe(false);
  });
});

// --- validateRequired ---

describe("validateRequired", () => {
  it("유효한 값은 null을 반환한다", () => {
    expect(validateRequired("값", "필드명")).toBeNull();
  });

  it("빈 문자열은 에러 메시지를 반환한다", () => {
    expect(validateRequired("", "거래처명")).toBe(
      "거래처명은(는) 필수 항목입니다",
    );
  });

  it("공백만 있는 문자열도 에러로 처리한다", () => {
    expect(validateRequired("   ", "이름")).toBe("이름은(는) 필수 항목입니다");
  });
});

// --- validateFileSize ---

describe("validateFileSize", () => {
  const createFile = (sizeKB: number) =>
    new File(["x".repeat(sizeKB * 1024)], "test.png", { type: "image/png" });

  it("제한 내 파일은 true를 반환한다", () => {
    expect(validateFileSize(createFile(50), 100)).toBe(true);
  });

  it("제한 초과 파일은 false를 반환한다", () => {
    expect(validateFileSize(createFile(200), 100)).toBe(false);
  });

  it("경계값(정확히 제한)은 true를 반환한다", () => {
    expect(validateFileSize(createFile(100), 100)).toBe(true);
  });
});
