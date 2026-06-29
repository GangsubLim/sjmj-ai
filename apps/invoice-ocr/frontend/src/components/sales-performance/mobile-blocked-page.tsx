import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Monitor } from "lucide-react";

export function MobileBlockedPage() {
  return (
    <div className="flex min-h-[60dvh] flex-col items-center justify-center gap-4 px-6 text-center">
      <Monitor className="text-muted-foreground size-12" />
      <h1 className="text-xl font-semibold">데스크탑에서 열어주세요</h1>
      <p className="text-muted-foreground">
        실적 달력은 데스크탑 화면 (1024px 이상) 에서만 제공됩니다.
      </p>
      <Button asChild>
        <Link to="/">홈으로 돌아가기</Link>
      </Button>
    </div>
  );
}
