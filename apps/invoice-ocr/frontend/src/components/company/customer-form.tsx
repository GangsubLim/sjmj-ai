import * as React from "react";
import {
  BuildingIcon,
  PhoneIcon,
  IdCardIcon,
  MapPinIcon,
  TruckIcon,
  FileTextIcon,
  SaveIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";

import type { Company, SmsNumberType } from "@/types/company";
import { formatBusinessNumber, formatPhoneNumber } from "@/utils/formatters";
import { validateBusinessNumber } from "@/utils/validators";
import { companySuggestionsAPI } from "@/services/api";

import { IconInput } from "@/components/ui/icon-input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { PageHeader, PageContainer } from "@/components/layout";

interface CustomerFormProps {
  initial?: Company;
  onSaved: () => void;
  onCancel: () => void;
  variant?: "page" | "panel";
}

function CustomerForm({
  initial,
  onSaved,
  onCancel,
  variant = "page",
}: CustomerFormProps) {
  const [companyName, setCompanyName] = React.useState(
    initial?.company_name ?? "",
  );
  const [phone, setPhone] = React.useState(initial?.phone ?? "");
  const [fax, setFax] = React.useState(initial?.fax ?? "");
  const [smsNumberType, setSmsNumberType] = React.useState<SmsNumberType>(
    initial?.sms_number_type ?? "phone",
  );
  const [businessNumber, setBusinessNumber] = React.useState(
    initial?.business_number ?? "",
  );
  const [address, setAddress] = React.useState(initial?.address ?? "");
  const [recipient2, setRecipient2] = React.useState(initial?.recipient2 ?? "");
  const [notes, setNotes] = React.useState(initial?.notes ?? "");
  const [saving, setSaving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false);
  const [isDirty, setIsDirty] = React.useState(false);
  const isInitialMount = React.useRef(true);

  React.useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    setIsDirty(true);
  }, [
    companyName,
    phone,
    fax,
    smsNumberType,
    businessNumber,
    address,
    recipient2,
    notes,
  ]);

  React.useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const bnValidation = validateBusinessNumber(businessNumber);

  const handleSave = async () => {
    if (!companyName.trim()) {
      toast.error("회사명을 입력해주세요");
      return;
    }
    setSaving(true);
    try {
      const data = {
        company_name: companyName,
        phone: phone || undefined,
        fax: fax || undefined,
        sms_number_type: smsNumberType,
        business_number: businessNumber.replace(/\D/g, "") || undefined,
        address: address || undefined,
        recipient2: recipient2 || undefined,
        notes: notes || undefined,
      };
      if (initial?.id) {
        await companySuggestionsAPI.update(initial.id, data);
        toast.success("거래처가 수정되었습니다");
      } else {
        await companySuggestionsAPI.add(data);
        toast.success("거래처가 추가되었습니다");
      }
      setIsDirty(false);
      onSaved();
    } catch {
      toast.error("저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!initial?.id) return;
    setDeleting(true);
    try {
      await companySuggestionsAPI.delete(initial.id);
      toast.success("거래처가 삭제되었습니다");
      setIsDirty(false);
      setShowDeleteDialog(false);
      onSaved();
    } catch {
      toast.error("삭제에 실패했습니다");
    } finally {
      setDeleting(false);
    }
  };

  const title = initial?.id ? "거래처 수정" : "거래처 추가";
  const saveLabel = saving ? "저장 중…" : initial?.id ? "수정" : "추가";
  const isPanel = variant === "panel";

  const formFields = (
    <div className="space-y-4">
      <div className="space-y-1">
        <Label
          htmlFor="customer-company-name"
          className="text-muted-foreground text-xs"
        >
          회사명 *
        </Label>
        <IconInput
          id="customer-company-name"
          icon={BuildingIcon}
          name="company_name"
          autoComplete="off"
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="회사명…"
        />
      </div>

      <div className="space-y-1">
        <Label
          htmlFor="customer-recipient2"
          className="text-muted-foreground text-xs"
        >
          대표자
        </Label>
        <IconInput
          id="customer-recipient2"
          icon={TruckIcon}
          name="recipient2"
          autoComplete="off"
          value={recipient2}
          onChange={(e) => setRecipient2(e.target.value)}
          placeholder="대표자…"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1">
          <Label
            htmlFor="customer-phone"
            className="text-muted-foreground text-xs"
          >
            연락처
          </Label>
          <IconInput
            id="customer-phone"
            icon={PhoneIcon}
            name="phone"
            autoComplete="off"
            type="tel"
            inputMode="tel"
            value={phone}
            onChange={(e) => setPhone(formatPhoneNumber(e.target.value))}
            placeholder="010-0000-0000"
          />
        </div>

        <div className="space-y-1">
          <Label
            htmlFor="customer-fax"
            className="text-muted-foreground text-xs"
          >
            FAX
          </Label>
          <IconInput
            id="customer-fax"
            icon={PhoneIcon}
            name="fax"
            autoComplete="off"
            type="tel"
            inputMode="tel"
            value={fax}
            onChange={(e) => setFax(formatPhoneNumber(e.target.value))}
            placeholder="02-0000-0000"
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-muted-foreground text-xs">문자 선택</Label>
        <RadioGroup
          value={smsNumberType}
          onValueChange={(value) => setSmsNumberType(value as SmsNumberType)}
          className="grid grid-cols-2 gap-2"
        >
          <label
            htmlFor="customer-sms-phone"
            className="border-border flex cursor-pointer items-center justify-center gap-2 rounded-lg border px-3 py-2 text-center"
          >
            <RadioGroupItem id="customer-sms-phone" value="phone" />
            <span className="text-sm font-medium">연락처</span>
          </label>
          <label
            htmlFor="customer-sms-fax"
            className="border-border flex cursor-pointer items-center justify-center gap-2 rounded-lg border px-3 py-2 text-center"
          >
            <RadioGroupItem id="customer-sms-fax" value="fax" />
            <span className="text-sm font-medium">FAX</span>
          </label>
        </RadioGroup>
      </div>

      <div className="space-y-1">
        <Label
          htmlFor="customer-business-number"
          className="text-muted-foreground text-xs"
        >
          사업자번호
        </Label>
        <IconInput
          id="customer-business-number"
          icon={IdCardIcon}
          name="business_number"
          autoComplete="off"
          spellCheck={false}
          inputMode="tel"
          value={formatBusinessNumber(businessNumber)}
          onChange={(e) =>
            setBusinessNumber(e.target.value.replace(/\D/g, "").slice(0, 10))
          }
          placeholder="000-00-00000"
        />
        {!bnValidation.valid && (
          <p className="mt-1 text-xs text-amber-500">{bnValidation.message}</p>
        )}
      </div>

      <div className="space-y-1">
        <Label
          htmlFor="customer-address"
          className="text-muted-foreground text-xs"
        >
          주소
        </Label>
        <div className="relative">
          <MapPinIcon className="text-muted-foreground pointer-events-none absolute top-3 left-3 size-4" />
          <Textarea
            id="customer-address"
            name="address"
            autoComplete="off"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="주소…"
            className="pl-10"
            rows={2}
          />
        </div>
      </div>

      <div className="space-y-1">
        <Label
          htmlFor="customer-notes"
          className="text-muted-foreground text-xs"
        >
          메모
        </Label>
        <div className="relative">
          <FileTextIcon className="text-muted-foreground pointer-events-none absolute top-3 left-3 size-4" />
          <Textarea
            id="customer-notes"
            name="notes"
            autoComplete="off"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="메모…"
            className="pl-10"
            rows={2}
          />
        </div>
      </div>
    </div>
  );

  const deleteDialog = (
    <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>거래처 삭제</AlertDialogTitle>
          <AlertDialogDescription>
            이 거래처를 삭제하시겠습니까?
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>취소</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={deleting}
          >
            {deleting ? "삭제 중…" : "삭제"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  if (isPanel) {
    return (
      <>
        <div className="p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">{title}</h2>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={onCancel}>
                취소
              </Button>
              {initial?.id && (
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:bg-destructive/10"
                  onClick={() => setShowDeleteDialog(true)}
                >
                  <Trash2Icon className="mr-1 size-4" aria-hidden="true" />
                  삭제
                </Button>
              )}
              <Button size="sm" onClick={handleSave} disabled={saving}>
                <SaveIcon className="mr-1 size-4" aria-hidden="true" />
                {saveLabel}
              </Button>
            </div>
          </div>
          {formFields}
        </div>
        {deleteDialog}
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={title}
        onBack={onCancel}
        rightAction={
          <div className="flex items-center gap-2">
            {initial?.id && (
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:bg-destructive/10"
                onClick={() => setShowDeleteDialog(true)}
              >
                <Trash2Icon className="mr-1 size-4" aria-hidden="true" />
                삭제
              </Button>
            )}
            <Button size="sm" onClick={handleSave} disabled={saving}>
              <SaveIcon className="mr-1 size-4" aria-hidden="true" />
              {saveLabel}
            </Button>
          </div>
        }
      />
      <PageContainer className="py-4">{formFields}</PageContainer>
      {deleteDialog}
    </>
  );
}

export { CustomerForm };
