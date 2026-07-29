import { cn } from "@/lib/utils";

interface CandidateChipProps {
  label: string;
  sim: number;
  rank: number;
  /** 현재 이 후보가 확정값으로 반영돼 있는지. 시각 강조 + aria-pressed로 노출한다. */
  selected: boolean;
  onSelect: (label: string, rank: number) => void;
  className?: string;
}

/**
 * OCR 후보 칩. 검수 큐레이션(CurationPairRow)과 작성 화면(ItemOcrCandidates)이 같은
 * 칩을 쓴다 — 접근 가능한 이름("후보 X, 유사도 Y")과 선택 표기가 한쪽만 바뀌어 두 화면의
 * UX가 조용히 갈라지는 것을 막으려고 한 곳에 모았다.
 *
 * mousedown을 preventDefault하는 이유: 칩 옆 입력창(Autocomplete)이 blur에서 값을
 * 커밋하므로, 그대로 두면 mousedown(칩)→blur(입력)→click(칩) 순서로 "입력 중이던 텍스트"가
 * 칩 선택보다 먼저 저장 요청으로 나간다. 포커스 이동을 막아 blur 커밋 자체를 없앤다.
 */
export function CandidateChip({
  label,
  sim,
  rank,
  selected,
  onSelect,
  className,
}: CandidateChipProps) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => onSelect(label, rank)}
      aria-label={`후보 ${label}, 유사도 ${sim.toFixed(2)}`}
      aria-pressed={selected}
      className={cn(
        "hover:bg-accent rounded-full border px-2 py-0.5",
        rank === 0 && "border-primary", // top1 표시(선택 상태와는 별개)
        selected && "border-primary bg-primary/10 font-medium",
        className,
      )}
    >
      {label} <span className="text-muted-foreground">{sim.toFixed(2)}</span>
    </button>
  );
}
