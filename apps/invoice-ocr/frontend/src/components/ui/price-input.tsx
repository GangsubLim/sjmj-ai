import * as React from "react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

function PriceInput({
  value,
  onChange,
  prefix = "",
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "value" | "onChange"> & {
  value: number | string;
  onChange: (value: number | string) => void;
  prefix?: string;
}) {
  const [focused, setFocused] = React.useState(false);

  const numericValue = typeof value === "string" ? parseInt(value) || 0 : value;
  const displayValue = focused
    ? value === 0 || value === ""
      ? ""
      : String(value)
    : numericValue === 0
      ? ""
      : `${prefix}${numericValue.toLocaleString("ko-KR")}`;

  return (
    <Input
      data-slot="price-input"
      type={focused ? "number" : "text"}
      inputMode="numeric"
      value={displayValue}
      onChange={(e) => {
        const raw = e.target.value.replace(/\D/g, "");
        onChange(raw === "" ? "" : parseInt(raw));
      }}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className={cn("text-right font-medium tabular-nums", className)}
      {...props}
    />
  );
}

export { PriceInput };
