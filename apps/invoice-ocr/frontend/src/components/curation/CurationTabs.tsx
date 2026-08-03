import { useNavigate } from "react-router-dom";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export type CurationTabValue = "confirmed" | "pending";

// 라우트로 가르므로 새로고침·북마크가 유지된다(탭 로컬 state가 아니다).
const TAB_ROUTES: Record<CurationTabValue, string> = {
  confirmed: "/curation",
  pending: "/curation/pending",
};

interface CurationTabsProps {
  active: CurationTabValue;
}

export function CurationTabs({ active }: CurationTabsProps) {
  const navigate = useNavigate();
  return (
    <Tabs
      value={active}
      onValueChange={(value) => navigate(TAB_ROUTES[value as CurationTabValue])}
      className="mb-3"
    >
      <TabsList>
        <TabsTrigger value="confirmed">확정 후</TabsTrigger>
        <TabsTrigger value="pending">확정 전</TabsTrigger>
      </TabsList>
    </Tabs>
  );
}
