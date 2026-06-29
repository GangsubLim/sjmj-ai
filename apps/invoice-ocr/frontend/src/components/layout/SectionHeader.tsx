import { cn } from "@/lib/utils";

interface SectionHeaderProps {
  title: string;
  rightContent?: React.ReactNode;
  className?: string;
}

export function SectionHeader({
  title,
  rightContent,
  className,
}: SectionHeaderProps) {
  return (
    <div className={cn("flex items-center justify-between py-3", className)}>
      <div className="flex items-center gap-3">
        <div className="bg-primary h-6 w-1 rounded-full" aria-hidden="true" />
        <h2 className="text-lg font-bold">{title}</h2>
      </div>
      {rightContent && <div>{rightContent}</div>}
    </div>
  );
}
