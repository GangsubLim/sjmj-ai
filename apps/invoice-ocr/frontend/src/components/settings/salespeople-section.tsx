import { useState } from "react";
import { toast } from "sonner";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useSalespeople } from "@/hooks/use-salespeople";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Trash2, Plus, ArrowUp, ArrowDown } from "lucide-react";

export function SalespeopleSection() {
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const { data, loading, error, create, update, softDelete } = useSalespeople();
  const [draftName, setDraftName] = useState("");

  if (!isDesktop) return null;

  const handleAdd = async () => {
    const name = draftName.trim();
    if (!name) return;
    try {
      await create({ name, sort_order: data.length });
      setDraftName("");
      toast.success("영업사원이 추가되었습니다.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "추가 실패");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await softDelete(id);
      toast.success("비활성화하면 신규 입력에서 숨겨지지만 기존 실적은 보존됩니다.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  const swapOrder = async (i: number, dir: -1 | 1) => {
    const a = data[i];
    const b = data[i + dir];
    if (!a || !b) return;
    await update(a.id!, { sort_order: b.sort_order });
    await update(b.id!, { sort_order: a.sort_order });
  };

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">영업사원 관리</h2>
      {error && <p className="text-destructive text-sm">{error}</p>}
      {loading && <p className="text-muted-foreground text-sm">로딩 중…</p>}

      <ul className="divide-border divide-y rounded-md border">
        {data.map((sp, idx) => (
          <li key={sp.id} className="flex items-center justify-between gap-2 p-3">
            <div className="flex items-center gap-2">
              <span className={sp.is_active ? "" : "text-muted-foreground"}>
                {sp.name}
                {!sp.is_active && " (비활성)"}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" onClick={() => swapOrder(idx, -1)} aria-label="위로">
                <ArrowUp className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" onClick={() => swapOrder(idx, 1)} aria-label="아래로">
                <ArrowDown className="size-4" />
              </Button>
              {sp.is_active === 1 && (
                <Button variant="ghost" size="icon" onClick={() => handleDelete(sp.id!)} aria-label="비활성화">
                  <Trash2 className="size-4" />
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <div className="flex gap-2">
        <Input
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          placeholder="영업사원 이름"
          onKeyDown={(e) => {
            if (e.key === "Enter") handleAdd();
          }}
        />
        <Button onClick={handleAdd}>
          <Plus className="mr-1 size-4" />
          추가
        </Button>
      </div>
    </section>
  );
}
