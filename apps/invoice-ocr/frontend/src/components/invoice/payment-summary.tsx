import { cn } from "@/lib/utils";
import { formatPrice } from "@/utils/formatters";

interface PaymentSummaryProps {
  totalSupply: number;
  totalVat: number;
  grandTotal: number;
  deductionTotal?: number;
  className?: string;
}

function PaymentSummary({
  totalSupply,
  totalVat,
  grandTotal,
  deductionTotal,
  className,
}: PaymentSummaryProps) {
  return (
    <div
      data-slot="payment-summary"
      className={cn("bg-surface-accent space-y-3 rounded-xl p-4", className)}
    >
      <p className="text-muted-foreground text-[10px] font-semibold tracking-widest uppercase">
        Payment Summary
      </p>

      <div className="space-y-1.5">
        <div className="flex justify-between text-sm">
          <span className="text-muted-foreground">공급가액</span>
          <span className="font-medium tabular-nums">
            {formatPrice(totalSupply)}원
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-muted-foreground">세액</span>
          <span className="font-medium tabular-nums">
            {formatPrice(totalVat)}원
          </span>
        </div>
        {deductionTotal !== undefined && deductionTotal !== 0 && (
          <div className="text-destructive flex justify-between text-sm">
            <span>공제액</span>
            <span className="font-medium tabular-nums">
              -{formatPrice(Math.abs(deductionTotal))}원
            </span>
          </div>
        )}
      </div>

      <div className="border-border/50 flex items-baseline justify-between border-t pt-3">
        <span className="text-sm font-semibold">합계</span>
        <div className="flex items-baseline gap-1">
          <span className="text-primary text-2xl font-extrabold tabular-nums">
            {formatPrice(grandTotal)}
          </span>
          <span className="text-muted-foreground text-xs">KRW</span>
        </div>
      </div>
    </div>
  );
}

export { PaymentSummary };
