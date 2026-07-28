import * as React from "react";

import type { OcrItemMeta } from "./ocr-prefill";
import { Button } from "@/components/ui/button";
import { CandidateChip } from "@/components/ocr/candidate-chip";
import { ocrCropUrl } from "@/services/api";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";

const PLACEHOLDER = placeholderSvg(64, 40);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

interface ItemOcrCandidatesProps {
  meta: OcrItemMeta;
  /** 현재 품목명. 어떤 후보가 반영돼 있는지 칩에 표시하기 위해 받는다. */
  selectedLabel: string;
  onPick: (label: string, rank: number) => void;
}

/**
 * 손글씨 크롭 + OCR 후보 칩. 판단 근거(원본 크롭)를 항상 곁들인다 —
 * 칩이 쉬워지는 만큼 오선택도 쉬워지기 때문이다(spec §목표와 우선순위).
 * 칩은 추가 수단이지 대체가 아니다: 품목명 Autocomplete(직접 입력)를 가리지 않는다.
 */
export function ItemOcrCandidates({
  meta,
  selectedLabel,
  onPick,
}: ItemOcrCandidatesProps) {
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
      {/* 접혔을 때도 컨테이너는 항상 마운트한다 — 조건부 마운트였을 때는 토글 버튼의
          aria-controls가 존재하지 않는 id를 가리켜 IDREF가 해소되지 않았다. hidden이
          접근성 트리에서 내용을 빼주므로 접힘 계약(칩이 role 조회에 안 잡힘)은 그대로다. */}
      <span
        id={listId}
        hidden={!expanded}
        className={expanded ? "contents" : undefined}
        data-slot="item-ocr-candidates-list"
      >
        {meta.candidates.length === 0 ? (
          <span className="text-muted-foreground text-xs">
            추천 후보가 없습니다 — 품목명을 직접 입력하세요
          </span>
        ) : (
          meta.candidates.map((pred, rank) => (
            <CandidateChip
              key={`${pred.label}-${rank}`}
              label={pred.label}
              sim={pred.sim}
              rank={rank}
              selected={pred.label === selectedLabel}
              onSelect={onPick}
              className="text-xs"
            />
          ))
        )}
      </span>
    </div>
  );
}
