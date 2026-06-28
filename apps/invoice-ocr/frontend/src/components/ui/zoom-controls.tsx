import * as React from "react";
import { MinusIcon, PlusIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

function ZoomControls({
  onZoomIn,
  onZoomOut,
  scale,
  className,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  scale: number;
  className?: string;
}) {
  return (
    <div
      data-slot="zoom-controls"
      className={cn(
        "bg-card fixed right-4 z-20 flex flex-col items-center gap-1 rounded-lg p-1 shadow-lg",
        className,
      )}
    >
      <Button variant="ghost" size="icon-xs" onClick={onZoomIn} aria-label="확대">
        <PlusIcon />
      </Button>
      <span className="text-muted-foreground text-xs font-medium tabular-nums">
        {Math.round(scale * 100)}%
      </span>
      <Button variant="ghost" size="icon-xs" onClick={onZoomOut} aria-label="축소">
        <MinusIcon />
      </Button>
    </div>
  );
}

export { ZoomControls };
