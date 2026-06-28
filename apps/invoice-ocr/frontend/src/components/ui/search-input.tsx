import * as React from "react";
import { SearchIcon, SlidersHorizontalIcon } from "lucide-react";

import { cn } from "@/lib/utils";

function SearchInput({
  value,
  onChange,
  onFilter,
  placeholder = "검색…",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  onFilter?: () => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div
      data-slot="search-input"
      className={cn("relative flex items-center", className)}
    >
      <SearchIcon className="text-muted-foreground pointer-events-none absolute left-4 size-5" aria-hidden="true" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label="검색"
        className="bg-muted placeholder:text-muted-foreground focus-visible:ring-ring h-12 w-full rounded-xl border pr-12 pl-12 text-base outline-none focus-visible:ring-2 md:text-sm"
      />
      {onFilter && (
        <button
          type="button"
          onClick={onFilter}
          aria-label="필터"
          className="text-muted-foreground hover:text-foreground absolute right-3 p-1"
        >
          <SlidersHorizontalIcon className="size-5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export { SearchInput };
