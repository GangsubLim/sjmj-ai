import { DownloadIcon, Trash2Icon } from "lucide-react";

import { cn } from "@/lib/utils";

function SelectionBar({
  count,
  onDownload,
  onDelete,
  className,
}: {
  count: number;
  onDownload?: () => void;
  onDelete?: () => void;
  className?: string;
}) {
  if (count === 0) return null;

  return (
    <div
      data-slot="selection-bar"
      className="fixed right-0 bottom-[72px] left-0 z-30 lg:bottom-0"
    >
      <div className="mx-auto max-w-md px-4 lg:max-w-none">
        <div
          className={cn(
            "bg-secondary text-background dark:bg-primary flex items-center justify-between rounded-t-xl p-2 shadow-lg transition-[background-color,box-shadow] duration-300",
            className,
          )}
        >
          <div className="flex items-center gap-2 pl-2">
            <span className="rounded-md bg-white/20 px-2 py-0.5 text-xs font-bold">
              {count}
            </span>
            <span className="text-sm font-medium">항목 선택됨</span>
          </div>
          <div className="flex gap-1">
            {onDownload && (
              <button
                onClick={onDownload}
                aria-label="다운로드"
                className="rounded-lg p-2 transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-white/50 focus-visible:outline-none"
              >
                <DownloadIcon className="size-5" />
              </button>
            )}
            {onDelete && (
              <button
                onClick={onDelete}
                aria-label="삭제"
                className="rounded-lg p-2 text-white transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-white/50 focus-visible:outline-none"
              >
                <Trash2Icon className="size-5" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export { SelectionBar };
