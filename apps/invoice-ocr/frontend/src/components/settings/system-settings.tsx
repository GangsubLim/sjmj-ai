import type { AppSettings } from "@/types/settings";

import { SectionHeader } from "@/components/layout";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface SystemSettingsProps {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}

function SystemSettings({ settings, onChange }: SystemSettingsProps) {
  const update = <K extends keyof AppSettings>(
    key: K,
    value: AppSettings[K],
  ) => {
    onChange({ ...settings, [key]: value });
  };

  return (
    <div className="space-y-6">
      <section>
        <SectionHeader title="기본 설정" />
        <div className="mt-2 space-y-3">
          <div className="space-y-1">
            <Label htmlFor="system-default-title" className="text-muted-foreground text-xs">
              기본 문서 제목
            </Label>
            <Input
              id="system-default-title"
              value={settings.default_document_title}
              onChange={(e) => update("default_document_title", e.target.value)}
              placeholder="거래명세서…"
            />
          </div>
        </div>
      </section>

      <section>
        <SectionHeader
          title="입금 계좌"
          rightContent={
            <Badge variant="secondary" className="text-xs">
              Coming Soon
            </Badge>
          }
        />
        <div className="bg-muted/50 text-muted-foreground mt-2 rounded-lg p-4 text-sm">
          입금 계좌 설정은 다음 업데이트에서 지원됩니다.
        </div>
      </section>

      <section>
        <SectionHeader
          title="PDF 파일명 패턴"
          rightContent={
            <Badge variant="secondary" className="text-xs">
              Coming Soon
            </Badge>
          }
        />
        <div className="bg-muted/50 text-muted-foreground mt-2 rounded-lg p-4 text-sm">
          PDF 파일명 패턴 설정은 다음 업데이트에서 지원됩니다.
        </div>
      </section>
    </div>
  );
}

export { SystemSettings };
