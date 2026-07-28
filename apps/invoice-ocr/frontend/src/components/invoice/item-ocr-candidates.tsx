import * as React from "react";

import type { OcrItemMeta } from "./ocr-prefill";
import { Button } from "@/components/ui/button";
import { ocrCropUrl } from "@/services/api";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";
import { cn } from "@/lib/utils";

const PLACEHOLDER = placeholderSvg(64, 40);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

interface ItemOcrCandidatesProps {
  meta: OcrItemMeta;
  onPick: (label: string, rank: number) => void;
}

/**
 * 손글씨 크롭 + OCR 후보 칩. 판단 근거(원본 크롭)를 항상 곁들인다 —
 * 칩이 쉬워지는 만큼 오선택도 쉬워지기 때문이다(spec §목표와 우선순위).
 * 칩은 추가 수단이지 대체가 아니다: 품목명 Autocomplete(직접 입력)를 가리지 않는다.
 */
export function ItemOcrCandidates({ meta, onPick }: ItemOcrCandidatesProps) {
  const [expanded, setExpanded] = React.useState(meta.uncertain);
  const listId = `item-ocr-candidates-list-${meta.jobId}-${meta.rowIndex}`;

  return (
    <div
      className="flex flex-wrap items-center gap-2"
      data-slot="item-ocr-candidates"
    >
      <img
        src={ocrCropUrl(meta.jobId, meta.rowIndex)}
        alt={`행 ${meta.rowIndex} 손글씨 크롭`}
        loading="lazy"
        className="h-10 w-16 shrink-0 rounded border object-cover"
        onError={handleImageError}
      />
      {meta.uncertain && (
        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
          <span aria-hidden="true">⚠</span> 미확신
        </span>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={() => setExpanded((prev) => !prev)}
      >
        후보 <span aria-hidden="true">▾</span>
      </Button>
      {expanded &&
        (meta.candidates.length === 0 ? (
          <span id={listId} className="text-muted-foreground text-xs">
            추천 후보가 없습니다 — 품목명을 직접 입력하세요
          </span>
        ) : (
          <span
            id={listId}
            className="contents"
            data-slot="item-ocr-candidates-list"
          >
            {meta.candidates.map((pred, rank) => (
              <button
                key={`${pred.label}-${rank}`}
                type="button"
                onClick={() => onPick(pred.label, rank)}
                aria-label={`후보 ${pred.label}, 유사도 ${pred.sim.toFixed(2)}`}
                className={cn(
                  "hover:bg-accent rounded-full border px-2 py-0.5 text-xs",
                  rank === 0 && "border-primary",
                )}
              >
                {pred.label}{" "}
                <span className="text-muted-foreground">
                  {pred.sim.toFixed(2)}
                </span>
              </button>
            ))}
          </span>
        ))}
    </div>
  );
}
