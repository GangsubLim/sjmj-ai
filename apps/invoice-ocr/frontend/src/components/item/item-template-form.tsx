import * as React from "react";
import {
  DropletIcon,
  CircleIcon,
  WrenchIcon,
  HardHatIcon,
  SaveIcon,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import type { Item } from "@/types/item";
import { itemSuggestionsAPI } from "@/services/api";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { PriceInput } from "@/components/ui/price-input";
import { PageHeader, PageContainer } from "@/components/layout";

const CATEGORIES = [
  { value: "oil", label: "오일", icon: DropletIcon },
  { value: "tires", label: "타이어", icon: CircleIcon },
  { value: "parts", label: "부품", icon: WrenchIcon },
  { value: "labor", label: "공임", icon: HardHatIcon },
] as const;

interface ItemTemplateFormProps {
  initial?: Item;
  onSaved: () => void;
  onCancel: () => void;
  variant?: "page" | "panel";
}

function ItemTemplateForm({
  initial,
  onSaved,
  onCancel,
  variant = "page",
}: ItemTemplateFormProps) {
  const [itemName, setItemName] = React.useState(initial?.item_name ?? "");
  const [category, setCategory] = React.useState<Item["category"]>(
    initial?.category,
  );
  const [defaultPrice, setDefaultPrice] = React.useState<number | string>(
    initial?.default_unit_price ?? 0,
  );
  const [notes, setNotes] = React.useState(initial?.notes ?? "");
  const [saving, setSaving] = React.useState(false);
  const [isDirty, setIsDirty] = React.useState(false);
  const isInitialMount = React.useRef(true);

  React.useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    setIsDirty(true);
  }, [itemName, category, defaultPrice, notes]);

  React.useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const handleSave = async () => {
    if (!itemName.trim()) {
      toast.error("품목명을 입력해주세요");
      return;
    }
    setSaving(true);
    try {
      const data = {
        item_name: itemName,
        default_unit_price:
          typeof defaultPrice === "string"
            ? parseInt(defaultPrice) || 0
            : defaultPrice,
        category,
        notes: notes || undefined,
      };
      if (initial?.id) {
        await itemSuggestionsAPI.update(initial.id, data);
        toast.success("품목이 수정되었습니다");
      } else {
        await itemSuggestionsAPI.add(data);
        toast.success("품목이 추가되었습니다");
      }
      setIsDirty(false);
      onSaved();
    } catch {
      toast.error("저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const title = initial?.id ? "품목 수정" : "새 품목";
  const saveLabel = saving ? "저장 중…" : initial?.id ? "수정" : "추가";
  const isPanel = variant === "panel";

  const formFields = (
    <div className="space-y-4">
      <div className="space-y-1">
        <Label htmlFor="item-name" className="text-muted-foreground text-xs">
          품목명 *
        </Label>
        <Input
          id="item-name"
          name="item_name"
          autoComplete="off"
          value={itemName}
          onChange={(e) => setItemName(e.target.value)}
          placeholder="품목명 입력…"
        />
      </div>

      <div className="space-y-2">
        <Label className="text-muted-foreground text-xs">카테고리</Label>
        <div className="grid grid-cols-2 gap-2">
          {CATEGORIES.map((cat) => {
            const active = category === cat.value;
            return (
              <button
                key={cat.value}
                type="button"
                onClick={() => setCategory(active ? undefined : cat.value)}
                className={cn(
                  "focus-visible:ring-ring flex h-28 flex-col items-center justify-center gap-2 rounded-xl border-2 transition-colors focus-visible:ring-2 focus-visible:outline-none",
                  active
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-muted-foreground/40",
                )}
              >
                <cat.icon
                  aria-hidden="true"
                  className={cn(
                    "size-8",
                    active ? "text-primary" : "text-muted-foreground",
                  )}
                />
                <span
                  className={cn(
                    "text-sm font-medium",
                    active ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  {cat.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="space-y-1">
        <Label
          htmlFor="item-default-price"
          className="text-muted-foreground text-xs"
        >
          기본 단가
        </Label>
        <PriceInput
          id="item-default-price"
          name="default_unit_price"
          autoComplete="off"
          value={defaultPrice}
          onChange={setDefaultPrice}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="item-notes" className="text-muted-foreground text-xs">
          메모
        </Label>
        <Textarea
          id="item-notes"
          name="notes"
          autoComplete="off"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="메모 (선택)…"
          rows={2}
        />
      </div>
    </div>
  );

  if (isPanel) {
    return (
      <div className="p-4">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onCancel}>
              취소
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving}>
              <SaveIcon className="mr-1 size-4" aria-hidden="true" />
              {saveLabel}
            </Button>
          </div>
        </div>
        {formFields}
      </div>
    );
  }

  return (
    <>
      <PageHeader
        title={title}
        onBack={onCancel}
        rightAction={
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <SaveIcon className="mr-1 size-4" aria-hidden="true" />
            {saveLabel}
          </Button>
        }
      />
      <PageContainer className="py-4">{formFields}</PageContainer>
    </>
  );
}

export { ItemTemplateForm };
