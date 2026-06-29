/**
 * 사업자번호 검증 (10자리 숫자)
 */
export function validateBusinessNumber(value: string): {
  valid: boolean;
  message?: string;
} {
  const digits = value.replace(/\D/g, "");
  if (digits.length === 0) return { valid: true };
  if (digits.length !== 10) {
    return { valid: false, message: "사업자번호는 10자리 숫자여야 합니다" };
  }
  return { valid: true };
}

/**
 * 필수 필드 검증
 */
export function validateRequired(
  value: string,
  fieldName: string,
): string | null {
  if (!value || value.trim().length === 0) {
    return `${fieldName}은(는) 필수 항목입니다`;
  }
  return null;
}

/**
 * 파일 크기 검증 (도장 업로드용)
 */
export function validateFileSize(file: File, maxKB: number): boolean {
  return file.size <= maxKB * 1024;
}
