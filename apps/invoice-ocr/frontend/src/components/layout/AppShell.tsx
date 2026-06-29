import { Component, type ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { useMediaQuery } from "@/hooks/use-media-query";
import { BottomNav } from "./BottomNav";
import { TopNav } from "./TopNav";
import { ErrorFallback } from "./ErrorFallback";

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

class ErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  handleReset = () => {
    this.setState({ hasError: false, error: undefined });
  };

  render() {
    if (this.state.hasError) {
      return (
        <ErrorFallback error={this.state.error} resetError={this.handleReset} />
      );
    }
    return this.props.children;
  }
}

export function AppShell() {
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  return (
    <div className="bg-background min-h-dvh">
      <a
        href="#main-content"
        className="bg-primary text-primary-foreground fixed top-0 left-1/2 z-[100] -translate-x-1/2 -translate-y-full rounded-b-md px-4 py-2 text-sm font-medium transition-transform focus:translate-y-0"
      >
        본문으로 건너뛰기
      </a>
      {isDesktop && <TopNav />}
      <main id="main-content" className={isDesktop ? "pt-14" : "pb-[72px]"}>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
      {!isDesktop && <BottomNav />}
      <Toaster position="top-center" richColors />
    </div>
  );
}
