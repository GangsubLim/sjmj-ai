import * as React from "react";
import { Link } from "react-router-dom";
import {
  CopyIcon,
  FileTextIcon,
  MessageSquareIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { Invoice } from "@/types/invoice";
import { formatPrice } from "@/utils/formatters";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

interface InvoiceCardProps {
  invoice: Invoice;
  selected?: boolean;
  smsTargetLabel?: string;
  onSelect?: (id: number, checked: boolean) => void;
  onClone?: (invoice: Invoice) => void;
  onDelete?: (id: number) => void;
  onPdf?: (invoice: Invoice) => void;
  onSmsCustomer?: (invoice: Invoice) => void;
  onSmsInternal?: (invoice: Invoice) => void;
}

function InvoiceCard({
  invoice,
  selected = false,
  smsTargetLabel,
  onSelect,
  onClone,
  onDelete,
  onPdf,
  onSmsCustomer,
  onSmsInternal,
}: InvoiceCardProps) {
  const date = invoice.issue_date
    ? new Date(invoice.issue_date).toLocaleDateString("ko-KR", {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : "";

  return (
    <div
      data-slot="invoice-card"
      className={cn(
        "group bg-card hover:border-primary/30 rounded-xl border p-4 shadow-sm transition-all hover:shadow-md",
        selected && "border-primary ring-primary/10 ring-2",
      )}
    >
      <div className="flex items-start gap-3">
        {onSelect && invoice.id && (
          <Checkbox
            checked={selected}
            onCheckedChange={(checked) =>
              invoice.id && onSelect(invoice.id, !!checked)
            }
            className="mt-1"
            aria-label={`${invoice.recipient} 선택`}
          />
        )}

        <div className="min-w-0 flex-1">
          {/* Row 1: Name + Amount */}
          <div className="flex items-start justify-between gap-2">
            <h3 className="truncate text-base font-bold">
              {invoice.recipient}
            </h3>
            <span className="text-primary shrink-0 text-lg font-bold tabular-nums">
              {formatPrice(invoice.grand_total)}
            </span>
          </div>

          {/* Row 2: Vehicle badge + Date */}
          <div className="mt-1 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              {invoice.vehicle_no && (
                <Badge variant="secondary" className="text-xs">
                  {invoice.vehicle_no}
                </Badge>
              )}
              {smsTargetLabel && (
                <span className="text-muted-foreground truncate text-xs">
                  문자: {smsTargetLabel}
                </span>
              )}
            </div>
            {date && (
              <span className="text-muted-foreground shrink-0 text-xs">
                {date}
              </span>
            )}
          </div>

          {/* Actions */}
          <div className="mt-3 -mb-1 flex items-center justify-between border-t pt-3">
            <div className="flex gap-1">
              <Link
                to={`/edit/${invoice.id}`}
                className={cn(
                  buttonVariants({ variant: "ghost", size: "xs" }),
                  "text-muted-foreground hover:text-primary",
                )}
              >
                <PencilIcon aria-hidden="true" />
                수정
              </Link>
              {onClone && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-muted-foreground hover:text-primary"
                  onClick={() => onClone(invoice)}
                >
                  <CopyIcon aria-hidden="true" />
                  복제
                </Button>
              )}
              {onPdf && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-muted-foreground hover:text-primary"
                  onClick={() => onPdf(invoice)}
                >
                  <FileTextIcon aria-hidden="true" />
                  PDF
                </Button>
              )}
              {onSmsCustomer && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-muted-foreground hover:text-primary"
                  onClick={() => onSmsCustomer(invoice)}
                >
                  <MessageSquareIcon aria-hidden="true" />
                  고객
                </Button>
              )}
              {onSmsInternal && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-muted-foreground hover:text-primary"
                  onClick={() => onSmsInternal(invoice)}
                >
                  <MessageSquareIcon aria-hidden="true" />
                  내부
                </Button>
              )}
            </div>
            {onDelete && invoice.id && (
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => invoice.id && onDelete(invoice.id)}
                className="text-muted-foreground hover:text-destructive"
                title="삭제"
                aria-label="삭제"
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export { InvoiceCard };
