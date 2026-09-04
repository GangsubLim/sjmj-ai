// ReactNode를 명시 import한다 — jsx runtime이 automatic이라 React 네임스페이스가 자동으로
// 들어오지 않는다(`React.ReactNode`는 tsc에서 TS2686으로 죽는다).
import { useState, type ReactNode } from "react";

import { curationImageUrl } from "@/services/api";
import { Skeleton } from "@/components/ui/skeleton";
import { useJobGeometry } from "@/hooks/use-job-geometry";
import type { StageGeometry } from "@/types/curation";
import {
  STAGE_PANEL_LABELS,
  amountCropRects,
  availablePanels,
  isSupportedGeometryVersion,
  itemCropRects,
  quadPoints,
  rowBandRects,
  type OverlayRect,
  type StagePanelId,
} from "@/utils/stage-geometry";

interface StageGeometryPanelProps {
  jobId: number;
}

function Notice({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <p className="font-bold">{title}</p>
      <p>{body}</p>
    </div>
  );
}

function RectOverlay({
  id,
  width,
  height,
  rects,
}: {
  id: StagePanelId;
  width: number;
  height: number;
  rects: OverlayRect[];
}) {
  return (
    <svg
      data-testid={`overlay-${id}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full"
    >
      {rects.map((r, i) => (
        <rect
          key={i}
          x={r.x}
          y={r.y}
          width={r.width}
          height={r.height}
          fill="none"
          stroke={r.color}
          strokeWidth={3}
        />
      ))}
    </svg>
  );
}

function LineOverlay({
  id,
  width,
  height,
  ys,
}: {
  id: StagePanelId;
  width: number;
  height: number;
  ys: number[];
}) {
  return (
    <svg
      data-testid={`overlay-${id}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full"
    >
      {ys.map((y) => (
        <line
          key={y}
          x1={0}
          x2={width}
          y1={y}
          y2={y}
          stroke="rgb(0, 130, 220)"
          strokeWidth={2}
        />
      ))}
    </svg>
  );
}

/** 이미지 한 장 + 그 위에 겹치는 오버레이 하나 — 좌표계는 호출자가 정한다. */
function StageFrame({
  label,
  src,
  alt,
  children,
}: {
  label: string;
  src: string;
  alt: string;
  children?: ReactNode;
}) {
  return (
    <div>
      <p className="text-muted-foreground mb-1 text-xs">{label}</p>
      <div className="relative">
        <img src={src} alt={alt} className="w-full rounded border" />
        {children}
      </div>
    </div>
  );
}

function GeometryStages({
  jobId,
  geometry,
}: {
  jobId: number;
  geometry: StageGeometry;
}) {
  const panels = availablePanels(geometry);
  const [enabled, setEnabled] = useState<StagePanelId[]>(panels);
  const on = (id: StagePanelId) => enabled.includes(id);
  const toggle = (id: StagePanelId) =>
    setEnabled((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  // 좌표계 두 벌 — 쿼드만 원본, 나머지는 워프. 파일이 진실이라 상수를 쓰지 않는다.
  const [imageW, imageH] = geometry.image_size;
  const [warpW, warpH] = geometry.warp_size;
  const original = curationImageUrl(jobId, "original");
  const warped = curationImageUrl(jobId, "warped");

  return (
    <div data-testid="stage-geometry-panel" className="space-y-3">
      <fieldset className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <legend className="sr-only">단계 패널 표시</legend>
        {panels.map((id) => (
          <label key={id} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={on(id)}
              onChange={() => toggle(id)}
              aria-label={STAGE_PANEL_LABELS[id]}
            />
            {STAGE_PANEL_LABELS[id]}
          </label>
        ))}
      </fieldset>

      {panels.includes("quad") && on("quad") && (
        <StageFrame
          label={`${STAGE_PANEL_LABELS.quad} (${geometry.quad_source ?? "?"})`}
          src={original}
          alt="쿼드 오버레이"
        >
          <svg
            data-testid="overlay-quad"
            viewBox={`0 0 ${imageW} ${imageH}`}
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 h-full w-full"
          >
            <polygon
              points={quadPoints(geometry) ?? ""}
              fill="none"
              stroke="rgb(255, 0, 0)"
              strokeWidth={Math.max(2, Math.round(imageW / 400))}
            />
          </svg>
        </StageFrame>
      )}

      {on("warp") && (
        <StageFrame
          label={`${STAGE_PANEL_LABELS.warp} (deskew ${geometry.deskew_deg?.toFixed(2) ?? "?"}°)`}
          src={warped}
          alt="워프 전표"
        />
      )}

      {panels.includes("rows") && on("rows") && (
        <StageFrame
          label={`${STAGE_PANEL_LABELS.rows} (행 피치 ${geometry.pitch?.toFixed(1) ?? "?"})`}
          src={warped}
          alt="행검출 오버레이"
        >
          <LineOverlay
            id="rows"
            width={warpW}
            height={warpH}
            ys={geometry.hlines ?? []}
          />
        </StageFrame>
      )}

      {panels.includes("rowClass") && on("rowClass") && (
        <StageFrame
          label={STAGE_PANEL_LABELS.rowClass}
          src={warped}
          alt="행 분류 오버레이"
        >
          <RectOverlay
            id="rowClass"
            width={warpW}
            height={warpH}
            rects={rowBandRects(geometry)}
          />
        </StageFrame>
      )}

      {panels.includes("itemCrop") && on("itemCrop") && (
        <StageFrame
          label={STAGE_PANEL_LABELS.itemCrop}
          src={warped}
          alt="품목 크롭 오버레이"
        >
          <RectOverlay
            id="itemCrop"
            width={warpW}
            height={warpH}
            rects={itemCropRects(geometry)}
          />
        </StageFrame>
      )}

      {panels.includes("amountCrop") && on("amountCrop") && (
        <StageFrame
          label={STAGE_PANEL_LABELS.amountCrop}
          src={warped}
          alt="금액 크롭 오버레이"
        >
          <RectOverlay
            id="amountCrop"
            width={warpW}
            height={warpH}
            rects={amountCropRects(geometry)}
          />
        </StageFrame>
      )}
    </div>
  );
}

/**
 * 단계 기하 패널 — 확정 후 상세 전용(spec §5-4).
 *
 * JobImagePanel을 확장하지 않는 것이 계약이다. 그 컴포넌트는 확정 전 상세와 공유하므로,
 * 거기서 기하를 열면 §8이 범위 밖으로 둔 확정 전 화면에 노출된다 — 신규 잡도 geometry
 * 파일을 가지므로 404 폴백이 성립하지 않는다.
 *
 * 응답 분기별로 다르게 닫는다: 404는 조용히 사라지고(상위의 원본·워프 2장이 폴백),
 * 500은 손상을 눈에 보이게 알리며(조용한 폴백 금지), 409는 이전 세대임을 알린다.
 */
export function StageGeometryPanel({ jobId }: StageGeometryPanelProps) {
  const state = useJobGeometry(jobId);

  if (state.status === "loading") return <Skeleton className="h-48 w-full" />;
  if (state.status === "absent") return null;
  if (state.status === "corrupt") {
    return (
      <Notice
        title="기하 파일 손상"
        body="이 잡의 단계 기하를 읽을 수 없습니다. 재처리로 다시 만들어야 합니다."
      />
    );
  }
  if (state.status === "stale") {
    return (
      <Notice
        title="이전 세대 기하"
        body="재처리가 끝나지 않아 화면의 기하가 현재 세대와 다릅니다. 재처리 성공 후 다시 열어 주세요."
      />
    );
  }
  if (state.status === "error") {
    return (
      <Notice
        title="단계 기하를 불러올 수 없습니다"
        body="잠시 뒤 새로고침해 주세요."
      />
    );
  }
  if (!isSupportedGeometryVersion(state.geometry)) {
    return (
      // 안내 문구("이 화면이 모르는 기하 형식입니다")는 body 하나에만 둔다 — 제목까지
      // 같은 말을 담으면 그 문구를 찾는 테스트가 요소 두 개에 걸려 계약이 흐려진다.
      <Notice
        title="기하 형식 불일치"
        body={`이 화면이 모르는 기하 형식입니다(version ${state.geometry.version}). 프론트 갱신이 필요합니다.`}
      />
    );
  }
  return <GeometryStages jobId={jobId} geometry={state.geometry} />;
}
