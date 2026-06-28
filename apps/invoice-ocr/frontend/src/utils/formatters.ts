import type { Invoice } from "@/types/invoice";
import type { Company, SmsNumberType } from "@/types/company";
import type { Issuer } from "@/types/settings";

/**
 * 금액 콤마 포맷 (UI 표시용)
 */
export function formatPrice(value: number): string {
  return value.toLocaleString("ko-KR");
}

/**
 * 숫자 포맷팅 (천단위 콤마) - formatPrice 통합
 */
export const formatNumber = formatPrice;

/**
 * 사업자번호 자동 포맷 (###-##-#####)
 */
export function formatBusinessNumber(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 10);
  if (digits.length <= 3) return digits;
  if (digits.length <= 5) return `${digits.slice(0, 3)}-${digits.slice(3)}`;
  return `${digits.slice(0, 3)}-${digits.slice(3, 5)}-${digits.slice(5)}`;
}

/**
 * 전화번호 포맷
 */
export function formatPhoneNumber(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length <= 3) return digits;
  if (digits.startsWith("02")) {
    if (digits.length <= 5) return `${digits.slice(0, 2)}-${digits.slice(2)}`;
    if (digits.length <= 9)
      return `${digits.slice(0, 2)}-${digits.slice(2, 5)}-${digits.slice(5)}`;
    return `${digits.slice(0, 2)}-${digits.slice(2, 6)}-${digits.slice(6, 10)}`;
  }
  if (digits.length <= 7) return `${digits.slice(0, 3)}-${digits.slice(3)}`;
  if (digits.length <= 10)
    return `${digits.slice(0, 3)}-${digits.slice(3, 6)}-${digits.slice(6)}`;
  return `${digits.slice(0, 3)}-${digits.slice(3, 7)}-${digits.slice(7, 11)}`;
}

/**
 * 금액 축약 표시 (달력 셀용)
 * 예: 1500 → "1.5k", 1200000 → "1.2M", 500 → "500"
 */
export function abbreviateAmount(value: number): string {
  if (value >= 1_000_000) {
    const m = value / 1_000_000;
    return m % 1 === 0 ? `${m}M` : `${m.toFixed(1)}M`;
  }
  if (value >= 1_000) {
    const k = value / 1_000;
    return k % 1 === 0 ? `${k}k` : `${k.toFixed(1)}k`;
  }
  return String(value);
}

export function getCompanySmsTargetValue(
  company: Company | null,
  smsNumberType?: SmsNumberType,
): string {
  if (!company) return "";
  const targetType = smsNumberType ?? company.sms_number_type ?? "phone";
  return (targetType === "fax" ? company.fax : company.phone) ?? "";
}

export function getCompanySmsTargetLabel(
  company: Company | null,
  smsNumberType?: SmsNumberType,
): string {
  if (!company) return "";
  const targetType = smsNumberType ?? company.sms_number_type ?? "phone";
  const targetValue = getCompanySmsTargetValue(company, targetType);

  if (!targetValue) {
    return targetType === "fax" ? "FAX 미등록" : "연락처 미등록";
  }

  return targetType === "fax" ? `FAX ${targetValue}` : `연락처 ${targetValue}`;
}

/**
 * SMS(고객) 입금 요청 메시지 생성
 */
export function buildSmsCustomerMessage(
  invoice: Invoice,
  company: Company | null,
  issuer: Issuer,
): string {
  const recipient2 = company?.recipient2 ?? "";
  const contactSuffix = recipient2 ? `(${recipient2})` : "";
  const amount = `${formatPrice(invoice.grand_total)}원`;

  return `안녕하세요 성진자동차입니다

${invoice.recipient}${contactSuffix}
${amount}(부가세포함)
입금부탁드립니다

입금계좌
${issuer.bank_account}
(주)성진자동차

감사합니다`;
}

/**
 * SMS(내부) 발행 알림 메시지 생성
 */
export function buildSmsInternalMessage(
  invoice: Invoice,
  company: Company | null,
  smsNumberType?: SmsNumberType,
): string {
  const recipient2 = company?.recipient2 ?? "";
  const contactSuffix = recipient2 ? `(${recipient2})` : "";
  const amount = `${formatPrice(invoice.grand_total)}원`;
  const smsTarget = getCompanySmsTargetValue(company, smsNumberType);

  const sendType = (smsNumberType ?? company?.sms_number_type) === "fax" ? "팩스" : "문자";

  return `${invoice.recipient}${contactSuffix}
${amount}(부가세포함)
계산서 발행했어요
거래명세서 ${sendType}완료입니다
${smsTarget}`;
}
