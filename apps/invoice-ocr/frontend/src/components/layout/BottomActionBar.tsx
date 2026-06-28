import { cn } from "@/lib/utils";

interface BottomActionBarProps {
  children: React.ReactNode;
  className?: string;
}

export function BottomActionBar({ children, className }: BottomActionBarProps) {
  return (
    <div className="bg-background/95 fixed right-0 bottom-0 left-0 z-50 border-t pb-[env(safe-area-inset-bottom)] shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.1)] backdrop-blur-sm lg:static lg:z-auto lg:border-0 lg:pb-0 lg:shadow-none lg:backdrop-blur-none">
      <div
        className={cn(
          "mx-auto grid max-w-md gap-3 p-4 lg:max-w-none",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
