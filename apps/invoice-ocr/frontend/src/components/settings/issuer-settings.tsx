import type { Issuer } from "@/types/settings";
import { formatBusinessNumber, formatPhoneNumber } from "@/utils/formatters";
import { validateBusinessNumber } from "@/utils/validators";

import { SectionHeader } from "@/components/layout";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { StampUpload } from "./stamp-upload";

interface IssuerSettingsProps {
  issuer: Issuer;
  onChange: (issuer: Issuer) => void;
}

function IssuerSettings({ issuer, onChange }: IssuerSettingsProps) {
  const update = <K extends keyof Issuer>(key: K, value: Issuer[K]) => {
    onChange({ ...issuer, [key]: value });
  };

  const bnValidation = validateBusinessNumber(issuer.business_number);

  return (
    <div className="space-y-6">
      {/* Business Profile */}
      <section>
        <SectionHeader title="사업자 정보" />
        <div className="mt-2 space-y-3">
          <StampUpload
            value={issuer.stamp_image_url}
            onChange={(url) => update("stamp_image_url", url)}
          />

          <div className="space-y-1">
            <Label htmlFor="issuer-company-name" className="text-muted-foreground text-xs">상호</Label>
            <Input
              id="issuer-company-name"
              name="issuer_company_name"
              autoComplete="off"
              value={issuer.company_name}
              onChange={(e) => update("company_name", e.target.value)}
              placeholder="상호명…"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="issuer-representative" className="text-muted-foreground text-xs">대표자</Label>
              <Input
                id="issuer-representative"
                name="issuer_representative"
                autoComplete="off"
                value={issuer.representative}
                onChange={(e) => update("representative", e.target.value)}
                placeholder="대표자명…"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="issuer-business-number" className="text-muted-foreground text-xs">
                사업자번호
              </Label>
              <Input
                id="issuer-business-number"
                name="issuer_business_number"
                autoComplete="off"
                spellCheck={false}
                value={formatBusinessNumber(issuer.business_number)}
                onChange={(e) =>
                  update(
                    "business_number",
                    e.target.value.replace(/\D/g, "").slice(0, 10),
                  )
                }
                placeholder="000-00-00000"
                className="font-mono"
              />
              {!bnValidation.valid && (
                <p className="text-xs text-amber-500">{bnValidation.message}</p>
              )}
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="issuer-address" className="text-muted-foreground text-xs">주소</Label>
            <Textarea
              id="issuer-address"
              name="issuer_address"
              autoComplete="off"
              value={issuer.address}
              onChange={(e) => update("address", e.target.value)}
              placeholder="사업장 주소…"
              rows={2}
            />
          </div>
        </div>
      </section>

      {/* Contact Details */}
      <section>
        <SectionHeader title="연락처 정보" />
        <div className="mt-2 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="issuer-business-type" className="text-muted-foreground text-xs">업태</Label>
              <Input
                id="issuer-business-type"
                name="issuer_business_type"
                autoComplete="off"
                value={issuer.business_type}
                onChange={(e) => update("business_type", e.target.value)}
                placeholder="업태…"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="issuer-business-item" className="text-muted-foreground text-xs">종목</Label>
              <Input
                id="issuer-business-item"
                name="issuer_business_item"
                autoComplete="off"
                value={issuer.business_item}
                onChange={(e) => update("business_item", e.target.value)}
                placeholder="종목…"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="issuer-phone" className="text-muted-foreground text-xs">전화</Label>
              <Input
                id="issuer-phone"
                name="issuer_phone"
                type="tel"
                autoComplete="off"
                value={issuer.phone}
                onChange={(e) =>
                  update("phone", formatPhoneNumber(e.target.value))
                }
                placeholder="전화번호…"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="issuer-fax" className="text-muted-foreground text-xs">팩스</Label>
              <Input
                id="issuer-fax"
                name="issuer_fax"
                type="tel"
                autoComplete="off"
                value={issuer.fax}
                onChange={(e) =>
                  update("fax", formatPhoneNumber(e.target.value))
                }
                placeholder="팩스번호…"
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="issuer-bank-account" className="text-muted-foreground text-xs">입금계좌</Label>
            <Input
              id="issuer-bank-account"
              name="issuer_bank_account"
              autoComplete="off"
              value={issuer.bank_account}
              onChange={(e) => update("bank_account", e.target.value)}
              placeholder="은행 계좌번호…"
            />
          </div>
        </div>
      </section>
    </div>
  );
}

export { IssuerSettings };
