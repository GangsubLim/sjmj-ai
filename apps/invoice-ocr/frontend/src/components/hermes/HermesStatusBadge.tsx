import { cn } from "@/lib/utils";
import {
  HERMES_STATUS_CLASSES,
  HERMES_STATUS_ICONS,
  HERMES_STATUS_LABELS,
} from "@/utils/hermes";
import type { HermesStatus } from "@/types/hermes";

// 목록·상세가 같은 배지를 쓴다 — 아이콘과 라벨이 한쪽에만 붙으면 같은 상태가 두 표기로
// 갈린다. 아이콘은 aria-hidden이라 접근 이름은 라벨 텍스트 하나로 유지된다.
export function HermesStatusBadge({
  status,
  className,
}: {
  status: HermesStatus;
  className?: string;
}) {
  const Icon = HERMES_STATUS_ICONS[status];
  return (
    <span
      data-testid="entry-status"
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap",
        HERMES_STATUS_CLASSES[status],
        className,
      )}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      {HERMES_STATUS_LABELS[status]}
    </span>
  );
}
