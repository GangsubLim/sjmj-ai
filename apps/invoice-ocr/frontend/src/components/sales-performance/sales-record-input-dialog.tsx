import { useMemo } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import type { Salesperson } from "@/types/salesperson";
import type { SalesRecord } from "@/types/sales-record";
import { useSalesInput } from "@/hooks/use-sales-input";

interface Props {
  open: boolean;
  date: string;
  salespeople: Salesperson[];
  recordsForDate: Map<number, SalesRecord> | undefined;
  onClose: () => void;
  onSave: (
    upserts: { salesperson_id: number; quantity: number }[],
    deletes: number[],
  ) => Promise<void>;
}

const formatComma = (raw: string): string => {
  const digits = raw.replace(/[^0-9]/g, "");
  if (digits === "") return "";
  return Number(digits).toLocaleString("ko-KR");
};

export function SalesRecordInputDialog({
  open,
  date,
  salespeople,
  recordsForDate,
  onClose,
  onSave,
}: Props) {
  const activeSps = useMemo(
    () => salespeople.filter((s) => s.is_active === 1),
    [salespeople],
  );
  const initial = useMemo(() => {
    const m = new Map<number, { id: number; quantity: number }>();
    if (recordsForDate) {
      for (const sp of activeSps) {
        const r = recordsForDate.get(sp.id!);
        if (r) m.set(sp.id!, { id: r.id, quantity: r.quantity });
      }
    }
    return m;
  }, [activeSps, recordsForDate]);

  const spIds = useMemo(() => activeSps.map((s) => s.id!), [activeSps]);
  const input = useSalesInput(initial, spIds);

  const handleSave = async () => {
    const mut = input.getMutations();
    await onSave(mut.upserts, mut.deletes);
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{date} 실적 입력</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          {activeSps.map((sp) => {
            const raw = input.values.get(sp.id!) ?? "";
            return (
              <div key={sp.id} className="flex items-center gap-2">
                <label className="w-24 text-sm font-medium">{sp.name}</label>
                <div className="relative flex-1">
                  <Input
                    value={formatComma(raw)}
                    inputMode="numeric"
                    pattern="[0-9]*"
                    placeholder="—"
                    onChange={(e) => input.setValue(sp.id!, e.target.value)}
                    className="pr-8 text-right"
                  />
                  {raw !== "" && (
                    <button
                      type="button"
                      onClick={() => input.setValue(sp.id!, "")}
                      className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2"
                      aria-label="비우기"
                    >
                      <X className="size-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={handleSave}>저장</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
