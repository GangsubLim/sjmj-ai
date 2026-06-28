import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface FilterOption {
  label: string;
  value: string;
  icon?: LucideIcon;
}

function FilterChips({
  options,
  value,
  onChange,
  className,
}: {
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div
      data-slot="filter-chips"
      className={cn(
        "scrollbar-none flex snap-x gap-2 overflow-x-auto pb-1",
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <Button
            key={opt.value}
            variant={active ? "filter-active" : "filter-inactive"}
            size="sm"
            onClick={() => onChange(opt.value)}
            className="shrink-0 snap-start"
          >
            {opt.icon && <opt.icon className="size-3.5" aria-hidden="true" />}
            {opt.label}
          </Button>
        );
      })}
    </div>
  );
}

export { FilterChips };
export type { FilterOption };
