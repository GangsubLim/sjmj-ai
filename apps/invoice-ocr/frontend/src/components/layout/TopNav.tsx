import { Link, NavLink } from "react-router-dom";
import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";

const navItems = [
  { label: "작성", path: "/" },
  { label: "목록", path: "/list" },
  { label: "거래처", path: "/companies" },
  { label: "품목", path: "/items" },
  { label: "실적", path: "/sales-performance" },
  { label: "설정", path: "/settings" },
] as const;

export function TopNav() {
  return (
    <nav
      aria-label="메인 내비게이션"
      className="bg-background/95 border-border fixed inset-x-0 top-0 z-40 border-b backdrop-blur-sm"
    >
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
        <div className="flex items-center gap-8">
          <Link
            to="/"
            className="text-primary text-lg font-bold tracking-tight"
          >
            SJMJ
          </Link>
          <div className="flex items-center gap-1">
            {navItems.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === "/"}
                className={({ isActive }) =>
                  cn(
                    "relative px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "text-primary after:bg-primary after:absolute after:inset-x-1 after:-bottom-[13px] after:h-0.5 after:rounded-full"
                      : "text-muted-foreground hover:text-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        </div>
        <Link to="/" className={buttonVariants({ size: "sm" })}>
          <Plus className="mr-1 size-4" aria-hidden="true" />새 명세서
        </Link>
      </div>
    </nav>
  );
}
