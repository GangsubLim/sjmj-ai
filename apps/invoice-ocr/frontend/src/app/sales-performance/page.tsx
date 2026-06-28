import { useMediaQuery } from "@/hooks/use-media-query";
import {
  SalesPerformanceCalendar,
  MobileBlockedPage,
} from "@/components/sales-performance";

export default function SalesPerformancePage() {
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  if (!isDesktop) return <MobileBlockedPage />;
  return <SalesPerformanceCalendar />;
}
