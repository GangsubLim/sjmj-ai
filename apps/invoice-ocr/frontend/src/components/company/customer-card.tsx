import { ChevronRightIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Company } from "@/types/company";
import { formatBusinessNumber } from "@/utils/formatters";

interface CustomerCardProps {
  company: Company;
  onClick?: () => void;
  className?: string;
}

function CustomerCard({ company, onClick, className }: CustomerCardProps) {
  const initial = company.company_name.charAt(0);

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group bg-card hover:border-primary/30 flex w-full items-start gap-4 rounded-xl border p-4 text-left shadow-sm transition-[box-shadow,border-color] hover:shadow-md",
        className,
      )}
    >
      <div className="bg-muted text-primary flex size-12 shrink-0 items-center justify-center rounded-full text-xl font-bold">
        {initial}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between">
          <h3 className="truncate pr-2 text-base leading-tight font-bold">
            {company.company_name}
          </h3>
          <ChevronRightIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden="true" />
        </div>
        {company.business_number && (
          <p className="text-muted-foreground mt-1 mb-2 text-xs font-medium">
            {formatBusinessNumber(company.business_number)}
          </p>
        )}
        <div className="border-border mt-1 grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-2 text-sm">
          <div>
            <span className="text-muted-foreground mb-0.5 block text-xs font-medium tracking-wider uppercase">
              대표자
            </span>
            <span className="text-foreground block truncate text-xs font-medium">
              {company.recipient2 ?? "-"}
            </span>
          </div>
          <div className="text-right">
            <span className="text-muted-foreground mb-0.5 block text-xs font-medium tracking-wider uppercase">
              {company.sms_number_type === "fax" ? "FAX" : "연락처"}
            </span>
            <span className="text-foreground block truncate text-xs font-medium">
              {company.sms_number_type === "fax"
                ? company.fax ?? "-"
                : company.phone ?? "-"}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}

export { CustomerCard };
