import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

function IconInput({
  icon: Icon,
  className,
  ...props
}: React.ComponentProps<"input"> & {
  icon: LucideIcon;
}) {
  return (
    <div data-slot="icon-input" className="relative">
      <Icon
        className="text-muted-foreground pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2"
        aria-hidden="true"
      />
      <Input className={cn("pl-11", className)} {...props} />
    </div>
  );
}

export { IconInput };
