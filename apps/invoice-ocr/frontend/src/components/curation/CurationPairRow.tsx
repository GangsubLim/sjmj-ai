import { useState } from "react";

import type { CurationJobPair, CurationPairPatch } from "@/types/curation";
import type { AutocompleteSuggestion } from "@/components/ui/autocomplete";
import { Autocomplete } from "@/components/ui/autocomplete";
import { Button } from "@/components/ui/button";
import { useItems } from "@/hooks/use-items";
import { useDebounce } from "@/hooks/use-debounce";
import { curationCropUrl } from "@/services/api";
import { isPairChanged } from "@/utils/curation";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";
import { cn } from "@/lib/utils";

const PLACEHOLDER = placeholderSvg(64, 40);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

interface CurationPairRowProps {
  jobId: number;
  pair: CurationJobPair;
  onPatch: (id: number, patch: CurationPairPatch) => void;
}

export function CurationPairRow({
  jobId,
  pair,
  onPatch,
}: CurationPairRowProps) {
  // 검색어 전용 로컬 state. 표시값(source of truth)은 pair.canonical_label이며
  // Autocomplete의 value 동기 effect가 서버 merge 후 입력창을 재동기한다.
  const [searchQuery, setSearchQuery] = useState(pair.canonical_label ?? "");
  const debounced = useDebounce(searchQuery, 250);
  const { data: items } = useItems(debounced);

  const suggestions: AutocompleteSuggestion[] = (items ?? []).map((it) => ({
    label: it.item_name,
    value: String(it.id ?? it.item_name),
  }));

  const excluded = pair.status === "excluded";

  // blur 시 옵티미스틱 PATCH. 실제 PATCH/롤백/top5 보존은 use-curation-job 훅이 소유.
  const commitLabel = (value: string) => {
    const next = value.trim();
    if (next && next !== (pair.canonical_label ?? "")) {
      onPatch(pair.id, { canonical_label: next });
    }
  };

  const toggleExcluded = () => {
    onPatch(pair.id, { status: excluded ? "included" : "excluded" });
  };

  return (
    <div
      className={cn(
        "flex flex-col gap-2 border-b py-3 sm:flex-row sm:items-start",
        excluded && "opacity-50",
      )}
      data-testid={`pair-${pair.id}`}
    >
      <img
        src={curationCropUrl(jobId, pair.row_index)}
        alt={`행 ${pair.row_index} crop`}
        className="h-10 w-16 rounded border object-cover"
        onError={handleImageError}
      />
      <div className="flex-1">
        <div className="text-muted-foreground mb-1 text-xs">
          #{pair.row_index} · top5:{" "}
          {pair.top5.map((t) => t.label).join("·") || "—"}
          {isPairChanged(pair) && (
            <span className="ml-1 text-amber-600">✎ 변경</span>
          )}
        </div>
        <Autocomplete
          value={pair.canonical_label ?? ""}
          onChange={setSearchQuery}
          onCommit={commitLabel}
          suggestions={suggestions}
          placeholder="라벨"
          ariaLabel={`행 ${pair.row_index} 라벨`}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">
          {pair.supply != null ? pair.supply.toLocaleString() : "—"}
          <span className="ml-1 text-xs">학습 비대상</span>
        </span>
        <Button size="sm" variant="outline" onClick={toggleExcluded}>
          {excluded ? "포함" : "제외"}
        </Button>
      </div>
    </div>
  );
}
