import * as React from "react";
import { Trash2Icon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { InvoiceItem } from "@/types/invoice";
import type { AutocompleteSuggestion } from "@/components/ui/autocomplete";
import { Autocomplete } from "@/components/ui/autocomplete";
import { PriceInput } from "@/components/ui/price-input";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { formatPrice } from "@/utils/formatters";

interface InvoiceItemRowProps {
  item: InvoiceItem;
  index: number;
  itemSuggestions: AutocompleteSuggestion[];
  onUpdate: (index: number, item: InvoiceItem) => void;
  onDelete: (index: number) => void;
  onAddNewItem?: (name: string) => void;
}

function InvoiceItemRow({
  item,
  index,
  itemSuggestions,
  onUpdate,
  onDelete,
  onAddNewItem,
}: InvoiceItemRowProps) {
  const isDeduction = item.deduction;

  const handleField = <K extends keyof InvoiceItem>(
    key: K,
    value: InvoiceItem[K],
  ) => {
    onUpdate(index, { ...item, [key]: value });
  };

  return (
    <div
      data-slot="invoice-item-row"
      className="overflow-hidden rounded-xl border"
    >
      {/* Header */}
      <div className="bg-muted/50 flex items-center gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <Autocomplete
            value={item.name}
            onChange={(val, suggestion) => {
              if (suggestion?.meta) {
                onUpdate(index, {
                  ...item,
                  name: val,
                  unit_price: parseInt(suggestion.meta) || 0,
                });
              } else {
                handleField("name", val);
              }
            }}
            suggestions={itemSuggestions}
            onAddNew={onAddNewItem}
            placeholder="품목명 입력"
            name={`item-${index}-name`}
            ariaLabel={`품목 ${index + 1} 이름`}
          />
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onDelete(index)}
          aria-label={`품목 ${index + 1} 삭제`}
          className="text-muted-foreground hover:text-destructive shrink-0"
        >
          <Trash2Icon aria-hidden="true" />
        </Button>
      </div>

      {/* Body */}
      <div className="space-y-3 px-3 py-3">
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label
              htmlFor={`item-${index}-quantity`}
              className="text-muted-foreground text-xs"
            >
              수량
            </Label>
            <Input
              id={`item-${index}-quantity`}
              name={`item-${index}-quantity`}
              type="number"
              inputMode="numeric"
              autoComplete="off"
              value={item.quantity === 0 ? "" : item.quantity}
              onChange={(e) =>
                handleField(
                  "quantity",
                  e.target.value === "" ? "" : e.target.value,
                )
              }
              placeholder="0"
              className="text-center"
            />
          </div>
          <div className="space-y-1">
            <Label
              htmlFor={`item-${index}-unit-price`}
              className="text-muted-foreground text-xs"
            >
              단가
            </Label>
            <PriceInput
              id={`item-${index}-unit-price`}
              name={`item-${index}-unit-price`}
              autoComplete="off"
              value={item.unit_price}
              onChange={(val) =>
                handleField("unit_price", val as number | string)
              }
            />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Switch
              id={`item-${index}-deduction`}
              checked={isDeduction}
              onCheckedChange={(checked) => handleField("deduction", checked)}
            />
            <Label
              htmlFor={`item-${index}-deduction`}
              className={cn("text-sm", isDeduction && "text-deduction-text")}
            >
              공제
            </Label>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="border-border flex items-center justify-between border-t border-dashed px-3 py-2">
        <span className="text-muted-foreground text-xs">
          세액 {formatPrice(Math.abs(item.vat))}원
        </span>
        <span
          className={cn(
            "text-sm font-semibold",
            isDeduction ? "text-deduction-text" : "text-primary",
          )}
        >
          {isDeduction && "-"}
          {formatPrice(Math.abs(item.total))}원
        </span>
      </div>
    </div>
  );
}

export { InvoiceItemRow };
