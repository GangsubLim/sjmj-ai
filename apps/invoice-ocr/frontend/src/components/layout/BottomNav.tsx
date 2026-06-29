import { useNavigate, NavLink } from "react-router-dom";
import { Receipt, Package, Plus, Users, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const tabs = [
  { label: "명세서", icon: Receipt, path: "/list" },
  { label: "품목", icon: Package, path: "/items" },
  { label: "fab", icon: Plus, path: "/" },
  { label: "거래처", icon: Users, path: "/companies" },
  { label: "설정", icon: Settings, path: "/settings" },
] as const;

export function BottomNav() {
  const navigate = useNavigate();

  return (
    <nav
      aria-label="하단 내비게이션"
      className="bg-background/95 border-border fixed right-0 bottom-0 left-0 z-40 touch-manipulation border-t backdrop-blur-sm"
    >
      <div className="mx-auto flex h-[72px] max-w-md items-center justify-between px-6">
        {tabs.map((tab) => {
          if (tab.label === "fab") {
            return (
              <button
                key="fab"
                onClick={() => navigate("/")}
                aria-label="새 거래명세서 작성"
                className="bg-primary shadow-primary/40 relative -top-6 flex h-14 w-14 items-center justify-center rounded-full text-white shadow-lg transition-transform hover:scale-105 active:scale-95"
              >
                <Plus className="size-8" aria-hidden="true" />
              </button>
            );
          }

          return (
            <NavLink
              key={tab.path}
              to={tab.path}
              className={({ isActive }) =>
                cn(
                  "flex flex-col items-center gap-1 px-3 py-2 transition-colors",
                  isActive ? "text-primary" : "text-muted-foreground",
                )
              }
            >
              <tab.icon className="size-6" aria-hidden="true" />
              <span className="text-[12px] font-medium">{tab.label}</span>
            </NavLink>
          );
        })}
      </div>
    </nav>
  );
}
