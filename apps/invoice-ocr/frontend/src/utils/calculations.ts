import { InvoiceItem } from "@/types/invoice";
export { formatNumber } from "@/utils/formatters";

/**
 * 공급가액과 부가세 계산
 * 단가는 세전 가격으로 처리
 */
export const calculateSupplyAndVat = (unitPrice: number, quantity: number) => {
  const supply = unitPrice * quantity;
  const vat = Math.round(supply * 0.1);
  const total = supply + vat;

  return {
    supply,
    vat,
    total,
  };
};

/**
 * 품목 계산
 */
export const calculateItem = (item: Partial<InvoiceItem>): InvoiceItem => {
  // 문자열인 경우 숫자로 변환, 빈 문자열이면 0 (계산용)
  const quantityForCalc =
    typeof item.quantity === "string"
      ? item.quantity === ""
        ? 0
        : Number(item.quantity) || 0
      : item.quantity || 0;

  const unitPriceForCalc =
    typeof item.unit_price === "string"
      ? item.unit_price === ""
        ? 0
        : Number(item.unit_price) || 0
      : item.unit_price || 0;

  const { supply, vat, total } = calculateSupplyAndVat(
    unitPriceForCalc,
    quantityForCalc,
  );

  // 공제 항목인 경우 음수로 처리
  const isDeduction = item.deduction || false;
  const finalSupply = isDeduction ? -supply : supply;
  const finalVat = isDeduction ? -vat : vat;
  const finalTotal = isDeduction ? -total : total;

  return {
    ...item,
    name: item.name || "",
    quantity: item.quantity !== undefined ? item.quantity : 0, // 원래 값 유지
    unit_price: item.unit_price !== undefined ? item.unit_price : 0, // 원래 값 유지
    supply: finalSupply,
    vat: finalVat,
    total: finalTotal,
    deduction: isDeduction,
  } as InvoiceItem;
};

/**
 * 전체 합계 계산
 */
export const calculateTotals = (items: InvoiceItem[]) => {
  const totalSupply = items.reduce((sum, item) => sum + item.supply, 0);
  const totalVat = items.reduce((sum, item) => sum + item.vat, 0);
  const grandTotal = items.reduce((sum, item) => sum + item.total, 0);

  return {
    total_supply: totalSupply,
    total_vat: totalVat,
    grand_total: grandTotal,
  };
};

/**
 * 숫자를 한글로 변환
 */
export const numberToKorean = (num: number): string => {
  if (num === 0) return "영";

  const units = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"];
  const positions = ["", "십", "백", "천"];
  const bigPositions = ["", "만", "억", "조"];

  const numStr = Math.abs(num).toString();
  const chunks: string[] = [];

  // 4자리씩 나누기
  for (let i = numStr.length; i > 0; i -= 4) {
    chunks.unshift(numStr.substring(Math.max(0, i - 4), i));
  }

  const result: string[] = [];

  chunks.forEach((chunk, chunkIndex) => {
    const chunkResult: string[] = [];
    const chunkNum = parseInt(chunk);

    if (chunkNum > 0) {
      Array.from(chunk).forEach((digit, digitIndex) => {
        const digitNum = parseInt(digit);
        const position = chunk.length - digitIndex - 1;

        if (digitNum > 0) {
          if (digitNum === 1 && position > 0) {
            chunkResult.push(positions[position]);
          } else {
            chunkResult.push(units[digitNum] + positions[position]);
          }
        }
      });

      const bigPosition = chunks.length - chunkIndex - 1;
      result.push(chunkResult.join("") + bigPositions[bigPosition]);
    }
  });

  return (num < 0 ? "마이너스 " : "") + result.join(" ") + " 원정";
};
