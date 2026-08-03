// 처리 관측 — 확정 전 잡의 읽기 전용 관측 타입.
// api-spec(.claude/ai-context/api-spec.json)의 OcrJobObservation을 미러한다.
// 드리프트 시 api-spec이 SSoT.

// 배지 8종. 백엔드 services/ocr_observation.py의 OBSERVATION_* 상수와 1:1.
export type ObservationStatus =
  | "pending"
  | "running"
  | "failed"
  | "no_result"
  | "no_warp"
  | "demoted"
  | "no_rows"
  | "unconfirmed";

export interface UnconfirmedJobSummary {
  job_id: number;
  observation_status: ObservationStatus;
  // rows가 배열일 때만 숫자. 추론 미완·계약 위반은 null — 화면은 0이 아니라 "—"로 그린다.
  row_count: number | null;
  error: string | null;
  created_at: string;
}
