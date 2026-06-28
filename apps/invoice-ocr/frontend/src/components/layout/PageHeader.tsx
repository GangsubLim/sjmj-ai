import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface PageHeaderProps {
  title: string;
  onBack?: () => void;
  showBack?: boolean;
  rightAction?: React.ReactNode;
  sticky?: boolean;
  transparent?: boolean;
  className?: string;
}

export function PageHeader({
  title,
  onBack,
  showBack = true,
  rightAction,
  sticky = true,
  transparent = false,
  className,
}: PageHeaderProps) {
  const navigate = useNavigate();

  const handleBack = () => {
    if (onBack) {
      onBack();
    } else {
      navigate(-1);
    }
  };

  return (
    <header
      className={cn(
        "z-20 lg:hidden",
        sticky && "sticky top-0",
        transparent
          ? "bg-transparent"
          : "border-border bg-background/95 border-b backdrop-blur-sm",
        className,
      )}
    >
      <div className="mx-auto flex h-14 max-w-md items-center justify-between px-4 lg:max-w-5xl">
        <div className="flex items-center gap-2">
          {showBack && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleBack}
              aria-label="뒤로 가기"
            >
              <ArrowLeft className="size-5" aria-hidden="true" />
            </Button>
          )}
          <h1 className="text-lg font-semibold">{title}</h1>
        </div>
        {rightAction && <div>{rightAction}</div>}
      </div>
    </header>
  );
}
