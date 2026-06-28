import * as React from "react";
import { CalendarIcon } from "lucide-react";
import { format, parse } from "date-fns";
import { ko } from "date-fns/locale";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

function DateInput({
  value,
  onChange,
  className,
  placeholder = "날짜 선택",
  id,
  name,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  placeholder?: string;
  id?: string;
  name?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const date = value ? parse(value, "yyyy-MM-dd", new Date()) : undefined;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {name && <input type="hidden" name={name} value={value} />}
      <PopoverTrigger asChild>
        <Button
          id={id}
          data-slot="date-input"
          variant="ghost"
          className={cn(
            "bg-muted hover:bg-muted/80 h-12 w-full justify-start rounded-xl border-0 text-left font-normal",
            !value && "text-muted-foreground",
            className,
          )}
        >
          <CalendarIcon className="mr-2 size-4" aria-hidden="true" />
          {date ? format(date, "yyyy년 M월 d일", { locale: ko }) : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={date}
          onSelect={(d) => {
            if (d) onChange(format(d, "yyyy-MM-dd"));
            setOpen(false);
          }}
          defaultMonth={date}
        />
      </PopoverContent>
    </Popover>
  );
}

export { DateInput };
