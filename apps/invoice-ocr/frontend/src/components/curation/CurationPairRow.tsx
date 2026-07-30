import { useState } from "react";

import type { CurationJobPair, CurationPairPatch } from "@/types/curation";
import type { AutocompleteSuggestion } from "@/components/ui/autocomplete";
import { Autocomplete } from "@/components/ui/autocomplete";
import { Button } from "@/components/ui/button";
import { useItems } from "@/hooks/use-items";
import { useDebounce } from "@/hooks/use-debounce";
import { ocrCropUrl } from "@/services/api";
import { isPairChanged } from "@/utils/curation";
import { CandidateChip } from "@/components/ocr/candidate-chip";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";
import { cn } from "@/lib/utils";

const PLACEHOLDER = placeholderSvg(148, 40);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

// 배지 표시 여부의 축은 "사유 != null"(= 기계 소유)이고, 문구만 사유별로 갈린다.
// ADR 0006이 사유 추가를 예고하므로 미지의 사유는 조용히 사라지지 않고 일반 문구로 뜬다.
const EXCLUSION_REASON_LABELS: Record<string, string | undefined> = {
  blank_crop: "빈 크롭 자동 배제",
};

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
        src={ocrCropUrl(jobId, pair.row_index)}
        alt={`행 ${pair.row_index} crop`}
        loading="lazy"
        className="h-10 w-37 shrink-0 rounded border object-contain"
        onError={handleImageError}
      />
      <div className="flex-1">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-1 text-xs">
          <span>#{pair.row_index}</span>
          {pair.uncertain && (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">
              <span aria-hidden="true">⚠</span> 미확신
            </span>
          )}
          {pair.exclusion_reason !== null && (
            <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700">
              <span aria-hidden="true">◻</span>{" "}
              {EXCLUSION_REASON_LABELS[pair.exclusion_reason] ?? "자동 배제"}
              {/* 사람이 포함으로 되돌린 칸(§6 셋째) — 행의 "제외" 버튼과 문구가 어긋나지 않게 명시. */}
              {!excluded && " (되돌림)"}
            </span>
          )}
          {pair.top5.length === 0 ? (
            <span>후보 없음</span>
          ) : (
            pair.top5.map((pred, rank) => (
              <CandidateChip
                key={`${pred.label}-${rank}`}
                label={pred.label}
                sim={pred.sim}
                rank={rank}
                selected={pred.label === (pair.canonical_label ?? "")}
                onSelect={(label) => {
                  // commitLabel(:44)과 동일 가드 — 이미 선택된 라벨에는 PATCH를 쏘지 않는다.
                  if (label === (pair.canonical_label ?? "")) return;
                  // 표시값 SSoT는 pair.canonical_label — 옵티미스틱 갱신이 입력창을 재동기하므로
                  // searchQuery(Autocomplete 제안 조회 전용)는 여기서 맞출 필요가 없다.
                  onPatch(pair.id, { canonical_label: label });
                }}
              />
            ))
          )}
          {isPairChanged(pair) && (
            <span className="text-amber-600">✎ 변경</span>
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
