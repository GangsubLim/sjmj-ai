import * as React from "react";
import { SaveIcon } from "lucide-react";
import { toast } from "sonner";

import type { Issuer, AppSettings } from "@/types/settings";
import { useSettings } from "@/hooks/use-settings";

import { PageContainer, PageHeader } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { IssuerSettings } from "@/components/settings";
import { SystemSettings } from "@/components/settings";
import { SalespeopleSection } from "@/components/settings";

const DEFAULT_ISSUER: Issuer = {
  company_name: "",
  representative: "",
  business_number: "",
  address: "",
  business_type: "",
  business_item: "",
  phone: "",
  fax: "",
  bank_account: "",
  tel_fax: "",
  show_sjdojang: true,
};

export default function SettingsPage() {
  const { issuer, appSettings, isLoaded, updateIssuer, updateAppSettings } =
    useSettings();

  const [localIssuer, setLocalIssuer] = React.useState<Issuer>(
    issuer ?? DEFAULT_ISSUER,
  );
  const [localSettings, setLocalSettings] =
    React.useState<AppSettings>(appSettings);
  const [saving, setSaving] = React.useState(false);

  const isDirty = React.useMemo(() => {
    if (!issuer) return false;
    return (
      JSON.stringify(localIssuer) !== JSON.stringify(issuer) ||
      JSON.stringify(localSettings) !== JSON.stringify(appSettings)
    );
  }, [localIssuer, issuer, localSettings, appSettings]);

  React.useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  React.useEffect(() => {
    if (issuer) setLocalIssuer(issuer);
  }, [issuer]);

  React.useEffect(() => {
    setLocalSettings(appSettings);
  }, [appSettings]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await Promise.all([
        updateIssuer(localIssuer),
        updateAppSettings(localSettings),
      ]);
      toast.success("설정이 저장되었습니다");
    } catch {
      toast.error("저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  if (!isLoaded) {
    return (
      <>
        <PageHeader title="설정" showBack={false} />
        <PageContainer className="space-y-4 py-4 lg:grid lg:grid-cols-2 lg:gap-8 lg:space-y-0">
          <div className="space-y-4">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        </PageContainer>
      </>
    );
  }

  const saveButton = (
    <Button size="sm" onClick={handleSave} disabled={saving}>
      <SaveIcon className="mr-1 size-4" aria-hidden="true" />
      {saving ? "저장 중…" : "저장"}
    </Button>
  );

  return (
    <>
      <PageHeader
        title="설정"
        showBack={false}
        rightAction={saveButton}
      />
      <PageContainer className="py-4">
        {/* PC 전용 페이지 헤더 */}
        <div className="hidden lg:flex lg:items-center lg:justify-between lg:pb-4">
          <h1 className="text-xl font-semibold">설정</h1>
          {saveButton}
        </div>
        <div className="space-y-6 lg:grid lg:grid-cols-2 lg:gap-8 lg:space-y-0">
          <IssuerSettings issuer={localIssuer} onChange={setLocalIssuer} />
          <SystemSettings settings={localSettings} onChange={setLocalSettings} />
          <SalespeopleSection />
        </div>
      </PageContainer>
    </>
  );
}
