import { cn } from "@/lib/utils";

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
  noPadding?: boolean;
}

export function PageContainer({
  children,
  className,
  noPadding,
}: PageContainerProps) {
  return (
    <div className={cn("mx-auto max-w-md lg:max-w-5xl", !noPadding && "px-4", className)}>
      {children}
    </div>
  );
}
