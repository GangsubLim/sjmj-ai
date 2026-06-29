import {
  DropletIcon,
  CircleIcon,
  WrenchIcon,
  HardHatIcon,
  FlameIcon,
  Trash2Icon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { Item } from "@/types/item";
import { formatPrice } from "@/utils/formatters";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const CATEGORY_CONFIG: Record<
  string,
  { icon: typeof DropletIcon; label: string; color: string }
> = {
  oil: {
    icon: DropletIcon,
    label: "오일",
    color: "bg-muted text-muted-foreground",
  },
  tires: {
    icon: CircleIcon,
    label: "타이어",
    color: "bg-muted text-muted-foreground",
  },
  parts: {
    icon: WrenchIcon,
    label: "부품",
    color: "bg-muted text-muted-foreground",
  },
  labor: {
    icon: HardHatIcon,
    label: "공임",
    color: "bg-muted text-muted-foreground",
  },
};

interface ItemTemplateCardProps {
  item: Item;
  onClick?: () => void;
  onDelete?: () => void;
  className?: string;
}

function ItemTemplateCard({
  item,
  onClick,
  onDelete,
  className,
}: ItemTemplateCardProps) {
  const cat = item.category ? CATEGORY_CONFIG[item.category] : null;
  const CatIcon = cat?.icon ?? WrenchIcon;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group bg-card hover:border-primary/30 flex w-full cursor-pointer items-center gap-3 rounded-xl border p-4 text-left shadow-sm transition-[box-shadow,border-color] hover:shadow-md",
        className,
      )}
    >
      <div className="text-muted-foreground flex size-12 shrink-0 items-center justify-center rounded-lg">
        <CatIcon className="size-6" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate text-base font-semibold">{item.item_name}</h3>
          {item.default_unit_price !== undefined &&
            item.default_unit_price > 0 && (
              <span className="text-primary shrink-0 text-lg font-bold tabular-nums">
                {formatPrice(item.default_unit_price)}
              </span>
            )}
        </div>
        <div className="mt-2 flex items-center gap-2 border-t pt-2">
          {cat && (
            <Badge variant="secondary" className="text-xs">
              {cat.label}
            </Badge>
          )}
          {item.usage_count !== undefined && item.usage_count >= 10 && (
            <span className="text-muted-foreground flex items-center gap-0.5 text-xs">
              <FlameIcon className="primary size-3" aria-hidden="true" />
              {item.usage_count}
            </span>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              className="text-muted-foreground hover:text-destructive ml-auto"
              title="삭제"
              aria-label="삭제"
            >
              <Trash2Icon aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </button>
  );
}

export { ItemTemplateCard, CATEGORY_CONFIG };
