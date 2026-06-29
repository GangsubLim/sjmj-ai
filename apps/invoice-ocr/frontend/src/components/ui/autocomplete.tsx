import * as React from "react";
import { PlusIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface AutocompleteSuggestion {
  label: string;
  value: string;
  meta?: string;
}

function Autocomplete({
  value,
  onChange,
  suggestions,
  onAddNew,
  placeholder = "검색…",
  className,
  id,
  name,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string, suggestion?: AutocompleteSuggestion) => void;
  suggestions: AutocompleteSuggestion[];
  onAddNew?: (value: string) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  name?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [inputValue, setInputValue] = React.useState(value);

  React.useEffect(() => {
    setInputValue(value);
  }, [value]);

  const filtered = suggestions.filter((s) =>
    s.label.toLowerCase().includes(inputValue.toLowerCase()),
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <div data-slot="autocomplete" className={cn("relative", className)}>
          <input
            id={id}
            name={name}
            className="bg-muted placeholder:text-muted-foreground focus-visible:ring-ring flex h-12 w-full rounded-xl px-4 py-1 text-base transition-[color,box-shadow] outline-none focus-visible:ring-2 md:text-sm"
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value);
              onChange(e.target.value);
              if (!open) setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            placeholder={placeholder}
            aria-label={ariaLabel ?? (id ? undefined : placeholder)}
          />
        </div>
      </PopoverTrigger>
      <PopoverContent
        className="w-[var(--radix-popover-trigger-width)] p-0"
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <Command shouldFilter={false}>
          <CommandList>
            {filtered.length === 0 && !onAddNew && (
              <CommandEmpty>결과 없음</CommandEmpty>
            )}
            <CommandGroup>
              {filtered.map((s) => (
                <CommandItem
                  key={s.value}
                  onSelect={() => {
                    onChange(s.label, s);
                    setInputValue(s.label);
                    setOpen(false);
                  }}
                >
                  <span className="flex-1">{s.label}</span>
                  {s.meta && (
                    <span className="text-muted-foreground text-xs">
                      {s.meta}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
            {onAddNew && inputValue && filtered.length === 0 && (
              <CommandGroup>
                <CommandItem
                  onSelect={() => {
                    onAddNew(inputValue);
                    setOpen(false);
                  }}
                >
                  <PlusIcon className="mr-2 size-4" aria-hidden="true" />
                  <span>"{inputValue}" 새로 추가</span>
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export { Autocomplete };
export type { AutocompleteSuggestion };
