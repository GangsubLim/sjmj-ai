import { curationImageUrl } from "@/services/api";
import { placeholderSvg, fallbackToPlaceholder } from "@/utils/placeholder";

const PLACEHOLDER = placeholderSvg(240, 160);
const handleImageError = fallbackToPlaceholder(PLACEHOLDER);

interface JobImagePanelProps {
  jobId: number;
}

// 원본·워프 전표 패널 — 확정 후 상세와 확정 전 상세가 공유한다(읽기 전용).
// warp_ok로 분기하지 않고 항상 시도한 뒤 404면 placeholder로 폴백한다:
// 파일이 있는데도 가리던 결함이 없어지고, "강등이냐 워프 산출이 없느냐"는
// 이미지가 뜨는지 여부 자체가 답한다.
export function JobImagePanel({ jobId }: JobImagePanelProps) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-muted-foreground mb-1 text-xs">① 원본</p>
        <img
          src={curationImageUrl(jobId, "original")}
          alt="원본 전표"
          className="w-full rounded border"
          onError={handleImageError}
        />
      </div>
      <div>
        <p className="text-muted-foreground mb-1 text-xs">② Warp</p>
        <img
          src={curationImageUrl(jobId, "warped")}
          alt="워프 전표"
          className="w-full rounded border"
          onError={handleImageError}
        />
      </div>
    </div>
  );
}
