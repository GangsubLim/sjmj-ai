import * as React from "react";
import { useNavigate } from "react-router-dom";
import {
  CarIcon,
  PlusIcon,
  FileTextIcon,
  SaveIcon,
  RotateCcwIcon,
} from "lucide-react";
import { toast } from "sonner";

import type { Invoice, InvoiceItem } from "@/types/invoice";
import type { AutocompleteSuggestion } from "@/components/ui/autocomplete";
import { calculateItem, calculateTotals } from "@/utils/calculations";
import { useCompanies } from "@/hooks/use-companies";
import { useItems } from "@/hooks/use-items";
import { useDebounce } from "@/hooks/use-debounce";
import { useSettings } from "@/hooks/use-settings";
import { useMediaQuery } from "@/hooks/use-media-query";
import { invoiceAPI } from "@/services/api";

import { PageHeader, PageContainer, SectionHeader } from "@/components/layout";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { IconInput } from "@/components/ui/icon-input";
import { DateInput } from "@/components/ui/date-input";
import { Autocomplete } from "@/components/ui/autocomplete";
import { InvoiceItemRow } from "./invoice-item-row";
import { PaymentSummary } from "./payment-summary";
import { InvoiceDocument } from "./invoice-document";

type ItemWithKey = InvoiceItem & { _tempId: string };

const DEFAULT_ITEM: InvoiceItem = {
  name: "",
  quantity: 1,
  unit_price: 0,
  supply: 0,
  vat: 0,
  total: 0,
  item_order: 0,
  deduction: false,
};

interface InvoiceFormProps {
  initialData?: Invoice;
  mode: "create" | "edit";
}

function InvoiceForm({ initialData, mode }: InvoiceFormProps) {
  const navigate = useNavigate();
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const { appSettings, issuer } = useSettings();

  const getDefaultIssueDate = React.useCallback(
    () => new Date().toISOString().slice(0, 10),
    [],
  );

  const buildInitialItems = React.useCallback(
    (data?: Invoice) =>
      data?.items?.length
        ? data.items.map((item) => ({
            ...item,
            _tempId: crypto.randomUUID(),
          }))
        : [{ ...DEFAULT_ITEM, item_order: 0, _tempId: crypto.randomUUID() }],
    [],
  );

  // Form state
  const [documentTitle, setDocumentTitle] = React.useState(
    initialData?.document_title ?? appSettings.default_document_title,
  );
  const [issueDate, setIssueDate] = React.useState(
    initialData?.issue_date ?? getDefaultIssueDate(),
  );
  const [showStamp, setShowStamp] = React.useState(
    initialData?.show_stamp ?? true,
  );
  const [recipient, setRecipient] = React.useState(
    initialData?.recipient ?? "",
  );
  const [recipient2, setRecipient2] = React.useState(
    initialData?.recipient2 ?? "",
  );
  const [vehicleNo, setVehicleNo] = React.useState(
    initialData?.vehicle_no ?? "",
  );
  const [memo, setMemo] = React.useState(initialData?.memo ?? "");
  const [items, setItems] = React.useState<ItemWithKey[]>(buildInitialItems(initialData));
  const [saving, setSaving] = React.useState(false);
  const [isDirty, setIsDirty] = React.useState(false);
  const isInitialMount = React.useRef(true);

  const itemsKey = JSON.stringify(items.map(i => ({ n: i.name, q: i.quantity, p: i.unit_price, d: i.deduction })));

  React.useEffect(() => {
    setDocumentTitle(initialData?.document_title ?? appSettings.default_document_title);
    setIssueDate(initialData?.issue_date ?? getDefaultIssueDate());
    setShowStamp(initialData?.show_stamp ?? true);
    setRecipient(initialData?.recipient ?? "");
    setRecipient2(initialData?.recipient2 ?? "");
    setVehicleNo(initialData?.vehicle_no ?? "");
    setMemo(initialData?.memo ?? "");
    setItems(buildInitialItems(initialData));
    setIsDirty(false);
    isInitialMount.current = true;
  }, [initialData, appSettings.default_document_title, getDefaultIssueDate, buildInitialItems]);

  React.useEffect(() => {
    if (isInitialMount.current) { isInitialMount.current = false; return; }
    setIsDirty(true);
  }, [documentTitle, issueDate, showStamp, recipient, recipient2, vehicleNo, memo, itemsKey]);

  React.useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => { if (isDirty) { e.preventDefault(); } };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Autocomplete data
  const [companyQuery, setCompanyQuery] = React.useState("");
  const debouncedCompanyQuery = useDebounce(companyQuery, 300);
  const { data: companies } = useCompanies(debouncedCompanyQuery);

  const [itemQuery] = React.useState("");
  const debouncedItemQuery = useDebounce(itemQuery, 300);
  const { data: itemSuggestions } = useItems(debouncedItemQuery);

  const companySuggestions: AutocompleteSuggestion[] = (companies ?? []).map(
    (c) => ({
      label: c.company_name,
      value: String(c.id ?? c.company_name),
      meta: c.recipient2,
    }),
  );

  const itemAutoSuggestions: AutocompleteSuggestion[] = (
    itemSuggestions ?? []
  ).map((i) => ({
    label: i.item_name,
    value: String(i.id ?? i.item_name),
    meta: i.default_unit_price ? String(i.default_unit_price) : undefined,
  }));

  // Recalculate items
  const calculatedItems = React.useMemo(
    () => items.map((item) => calculateItem(item)),
    [items],
  );

  const totals = React.useMemo(
    () => calculateTotals(calculatedItems),
    [calculatedItems],
  );

  const deductionTotal = React.useMemo(
    () =>
      calculatedItems
        .filter((i) => i.deduction)
        .reduce((sum, i) => sum + i.total, 0),
    [calculatedItems],
  );

  // Handlers
  const handleItemUpdate = (index: number, updated: InvoiceItem) => {
    setItems((prev) => {
      const next = [...prev];
      next[index] = { ...updated, _tempId: next[index]._tempId };
      return next;
    });
  };

  const handleItemDelete = (index: number) => {
    setItems((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((_, i) => i !== index);
    });
  };

  const handleAddItem = () => {
    setItems((prev) => [
      ...prev,
      {
        ...DEFAULT_ITEM,
        item_order: prev.length,
        _tempId: crypto.randomUUID(),
      },
    ]);
  };

  const handleCompanySelect = (
    value: string,
    suggestion?: AutocompleteSuggestion,
  ) => {
    setRecipient(value);
    setCompanyQuery(value);
    if (suggestion?.meta) {
      setRecipient2(suggestion.meta);
    }
  };

  const handleSave = async () => {
    if (!recipient.trim()) {
      toast.error("거래처명을 입력해주세요");
      return;
    }
    const validItems = calculatedItems.filter((i) => i.name.trim());
    if (validItems.length === 0) {
      toast.error("최소 1개 이상의 품목을 입력해주세요");
      return;
    }

    setSaving(true);
    try {
      const payload = {
        document_title: documentTitle,
        issue_date: issueDate,
        recipient,
        recipient2,
        vehicle_no: vehicleNo,
        show_stamp: showStamp,
        memo,
        items: validItems.map((item, i) => {
          const { _tempId, ...rest } = item as InvoiceItem & {
            _tempId?: string;
          };
          return { ...rest, item_order: i };
        }),
        total_supply: totals.total_supply,
        total_vat: totals.total_vat,
        grand_total: totals.grand_total,
      };

      if (mode === "edit" && initialData?.id) {
        await invoiceAPI.update(initialData.id, payload);
        toast.success("거래명세서가 수정되었습니다");
      } else {
        await invoiceAPI.create(payload);
        toast.success("거래명세서가 저장되었습니다");
      }
      setIsDirty(false);
      navigate("/list");
    } catch {
      toast.error("저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setDocumentTitle(appSettings.default_document_title);
    setIssueDate(getDefaultIssueDate());
    setShowStamp(true);
    setRecipient("");
    setRecipient2("");
    setVehicleNo("");
    setMemo("");
    setItems([
      { ...DEFAULT_ITEM, item_order: 0, _tempId: crypto.randomUUID() },
    ]);
  };

  // PC 미리보기용 deferred state
  const previewInvoice = React.useMemo<Invoice>(
    () => ({
      document_title: documentTitle,
      issue_date: issueDate,
      recipient,
      recipient2: recipient2 || undefined,
      vehicle_no: vehicleNo,
      show_stamp: showStamp,
      memo: memo || undefined,
      items: calculatedItems,
      total_supply: totals.total_supply,
      total_vat: totals.total_vat,
      grand_total: totals.grand_total,
    }),
    [
      documentTitle,
      issueDate,
      recipient,
      recipient2,
      vehicleNo,
      showStamp,
      memo,
      calculatedItems,
      totals,
    ],
  );

  const deferredPreview = React.useDeferredValue(previewInvoice);

  // ResizeObserver for A4 scaling
  const previewContainerRef = React.useRef<HTMLDivElement>(null);
  const documentRef = React.useRef<HTMLDivElement>(null);
  const [previewScale, setPreviewScale] = React.useState(1);

  React.useEffect(() => {
    if (!isDesktop) return;
    const container = previewContainerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      const { width } = entries[0].contentRect;
      const padding = 32; // 16px * 2
      setPreviewScale(Math.min((width - padding) / 595, 1));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [isDesktop]);

  const formTitle = mode === "create" ? "거래명세서 작성" : "거래명세서 수정";

  const formSections = (
    <div className="space-y-6">
      {/* Section 1: Document Info */}
      <section>
        <SectionHeader title="문서 정보" />
        <div className="mt-2 space-y-3">
          <div className="space-y-1">
            <Label htmlFor="invoice-document-title" className="text-muted-foreground text-xs">
              문서 제목
            </Label>
            <Input
              id="invoice-document-title"
              name="document_title"
              autoComplete="off"
              value={documentTitle}
              onChange={(e) => setDocumentTitle(e.target.value)}
              placeholder="거래명세서…"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="invoice-issue-date" className="text-muted-foreground text-xs">
                발행일
              </Label>
              <DateInput id="invoice-issue-date" name="issue_date" value={issueDate} onChange={setIssueDate} />
            </div>
            <div className="flex h-12 items-center justify-end gap-2 self-end">
              <Switch id="invoice-show-stamp" checked={showStamp} onCheckedChange={setShowStamp} />
              <Label htmlFor="invoice-show-stamp" className="text-sm">도장 표시</Label>
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="invoice-recipient" className="text-muted-foreground text-xs">
              거래처 (수신)
            </Label>
            <Autocomplete
              id="invoice-recipient"
              name="recipient"
              value={recipient}
              onChange={handleCompanySelect}
              suggestions={companySuggestions}
              placeholder="거래처명 입력"
            />
          </div>

          {recipient2 && (
            <div className="space-y-1">
              <Label htmlFor="invoice-recipient2" className="text-muted-foreground text-xs">
                수신참조
              </Label>
              <Input
                id="invoice-recipient2"
                name="recipient2"
                autoComplete="off"
                value={recipient2}
                onChange={(e) => setRecipient2(e.target.value)}
              />
            </div>
          )}

          <div className="space-y-1">
            <Label htmlFor="invoice-vehicle-no" className="text-muted-foreground text-xs">
              차량번호
            </Label>
            <IconInput
              id="invoice-vehicle-no"
              name="vehicle_no"
              autoComplete="off"
              icon={CarIcon}
              value={vehicleNo}
              onChange={(e) => setVehicleNo(e.target.value)}
              placeholder="예: 12가 3456, 34나 5678…"
              maxLength={255}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="invoice-memo" className="text-muted-foreground text-xs">
              메모/비고 (선택)
            </Label>
            <Textarea
              id="invoice-memo"
              name="memo"
              autoComplete="off"
              value={memo}
              onChange={(e) => setMemo(e.target.value)}
              placeholder="내부 메모용 (PDF에 선택적 출력)…"
              className="resize-none"
              rows={3}
            />
          </div>
        </div>
      </section>

      {/* Section 2: Parts & Labor */}
      <section>
        <SectionHeader title="품목 / 공임" />
        <div className="mt-2 space-y-3">
          {calculatedItems.map((item, index) => (
            <InvoiceItemRow
              key={items[index]._tempId}
              item={item}
              index={index}
              itemSuggestions={itemAutoSuggestions}
              onUpdate={handleItemUpdate}
              onDelete={handleItemDelete}
            />
          ))}
          <button
            type="button"
            onClick={handleAddItem}
            className="border-muted-foreground/30 text-muted-foreground hover:border-primary hover:text-primary focus-visible:ring-ring flex h-12 w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <PlusIcon className="size-4" aria-hidden="true" />
            <span className="text-sm font-medium">품목 추가</span>
          </button>
        </div>
      </section>

      {/* Payment Summary */}
      <PaymentSummary
        totalSupply={totals.total_supply}
        totalVat={totals.total_vat}
        grandTotal={totals.grand_total}
        deductionTotal={deductionTotal}
      />
    </div>
  );

  return (
    <>
      <PageHeader
        title={formTitle}
        showBack={false}
        rightAction={
          <div className="flex items-center gap-1">
            {mode === "edit" && (
              <Button variant="ghost" size="icon-sm" onClick={handleReset} aria-label="초기화">
                <RotateCcwIcon className="size-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="미리보기"
              onClick={() =>
                toast.info("미리보기는 저장 후 목록에서 이용 가능합니다")
              }
            >
              <FileTextIcon className="size-4" />
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving}>
              <SaveIcon className="mr-1 size-4" aria-hidden="true" />
              {saving ? "저장 중…" : "저장"}
            </Button>
          </div>
        }
      />
      <PageContainer className="py-4 lg:max-w-6xl">
        <div className="lg:grid lg:grid-cols-[5fr_7fr] lg:gap-6">
          {/* 좌측: 폼 */}
          <div>
            {/* PC 전용 인라인 헤더 */}
            <div className="hidden lg:flex lg:items-center lg:justify-between lg:pb-4">
              <h1 className="text-xl font-semibold">{formTitle}</h1>
              <div className="flex items-center gap-2">
                {mode === "edit" && (
                  <Button variant="outline" size="sm" onClick={handleReset}>초기화</Button>
                )}
                <Button size="sm" onClick={handleSave} disabled={saving}>
                  <SaveIcon className="mr-1 size-4" aria-hidden="true" />
                  {saving ? "저장 중…" : "저장"}
                </Button>
              </div>
            </div>
            {formSections}
          </div>

          {/* 우측: PC 실시간 미리보기 */}
          {isDesktop && issuer ? (
            <div
              ref={previewContainerRef}
              className="sticky top-14 h-[calc(100dvh-56px)] overflow-auto rounded-xl border bg-gray-100 p-4"
            >
              <div
                style={{
                  transform: `scale(${previewScale})`,
                  transformOrigin: "top center",
                }}
              >
                <InvoiceDocument
                  ref={documentRef}
                  invoice={deferredPreview}
                  issuer={issuer}
                />
              </div>
            </div>
          ) : null}
        </div>
      </PageContainer>
    </>
  );
}

export { InvoiceForm };
