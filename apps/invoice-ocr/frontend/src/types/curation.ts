// api-spec(.claude/ai-context/api-spec.json)의 Curation* 스키마를 미러한다.
// 드리프트 시 api-spec이 SSoT.

export interface CurationTop5Item {
  label: string;
  sim: number;
}

export interface CurationJobSummary {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  // 첫 검수 시각. 게이트가 해제돼도 지워지지 않아 "미검수"와 "재검수 필요"를 가른다.
  curation_reviewed_at: string | null;
  pair_count: number;
  unreviewed_count: number;
  // 확정 시 사람이 더한 행 수 / 버린 행 수(백엔드가 ocr_corrections에서 투영).
  // null은 관측 없음 — 증감 0과 섞지 않는다. 방향별로 움직여야 할 기준선이 정반대라
  // 합산하지 않고 갈라 센다. 판정이 아니라 어느 잡을 먼저 열지 고르는 힌트다.
  rows_added: number | null;
  rows_dropped: number | null;
  created_at: string;
}

// 잡 상세 pair와 PATCH 결과가 공유하는 공통 필드.
export interface CurationPairBase {
  id: number;
  crop_ref: string;
  row_index: number;
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
  supply: number | null;
  status: "included" | "excluded";
  // 기계 판정 배제 사유. null = 사람 판정(사유 미분류). 서버 전용 쓰기 — PATCH로 못 보낸다.
  // relink_failed = 재처리 승계 실패(그림 자체가 없다 — 빈 크롭과 다르다).
  exclusion_reason: "blank_crop" | "relink_failed" | null;
  reviewed_at: string | null;
}

// GET /jobs/{id} 의 pair — top5 + uncertain 포함, job_id 없음.
export interface CurationJobPair extends CurationPairBase {
  top5: CurationTop5Item[];
  // 품목 top1이 result_json의 item_conf_threshold 미만이거나 후보가 없을 때 true.
  // PATCH 응답에는 없다(top5와 같은 계약 비대칭) — patchPair merge가 기존 값을 보존한다.
  uncertain: boolean;
  // false면 승계에 실패한 미결 쌍이라 crop URL 자체를 만들지 않는다(spec §6-1).
  // 서버도 이 쌍을 새 행과 조인하지 않는다 — 둘 중 하나만 지켜도 화면이 조용히 어긋난다.
  crop_available: boolean;
}

// PATCH /pairs/{id} 응답 — job_id + 잡 게이트 + 갱신된 토큰 포함, top5 없음(계약 비대칭).
export interface CurationPairPatchResult extends CurationPairBase {
  job_id: number;
  // 쌍 수정은 그 잡의 게이트를 무조건 해제하므로 서버는 항상 false를 돌려준다.
  job_curation_reviewed: boolean;
  // 다음 PATCH에 실을 새 세대 토큰.
  job_token: string;
}

// 잡 처리 상태 — 백엔드 ocr_jobs.status 원값(api-spec CurationJobDetail.status).
// done이 아니면 서버가 쌍 PATCH·검수완료를 409로 거부한다.
export type CurationJobStatus = "pending" | "running" | "done" | "failed";

export interface CurationJobDetail {
  job_id: number;
  invoice_id: number | null;
  // done이 아니면 화면이 경고 배너를 띄운다(편집 차단은 하지 않는다 — 저장 시도는
  // 어차피 서버가 409로 막는다). optional로 두면 배너 분기가 조용히 사라진다.
  status: CurationJobStatus;
  curation_reviewed: boolean;
  curation_reviewed_at: string | null;
  warp_ok: boolean;
  created_at: string;
  // 잡 세대 토큰 — PATCH에 실어 보내지 않으면 400, 재처리 뒤의 값이면 409.
  job_token: string;
  pairs: CurationJobPair[];
}

// 컴포넌트가 만드는 부분 갱신 — 토큰은 없다(훅이 채운다).
export type CurationPairPatch = {
  status?: "included" | "excluded";
  canonical_label?: string;
};

// 실제로 와이어에 나가는 본문. job_token은 서버가 **필수**로 요구하므로 여기서도 필수다 —
// optional로 두면 훅이 토큰을 못 채운 창에서 axios가 키를 떨궈, 의도한 409(세대 충돌)
// 대신 400(형식 오류)이 나가고 세대 방어가 타입 차원에서 강제되지 않는다.
export type CurationPairPatchBody = CurationPairPatch & {
  job_token: string;
};

export type CurationImageKind = "original" | "warped";

// ── 단계 기하(ADR 0012) — 생산자는 ml 워커, 계약 정본은 handwriting/geometry.py ──
// 백엔드는 generation 한 키만 읽고 스키마를 검증하지 않는다. 아래 타입은 api-spec의
// StageGeometry를 미러하며, 워커가 도달하지 못한 단계의 키는 **부재**한다(null 아님).

// 이 값과 다른 version이 오면 화면은 패널을 렌더하지 않고 안내 문구로 닫는다.
// 워커의 handwriting/geometry.py:GEOMETRY_VERSION과 같은 값이어야 한다 — 이 동기는
// tests/test_geometry_version_sync.py가 CI에서 강제한다.
export const STAGE_GEOMETRY_VERSION = 1;

export type StageGeometryQuadSource = "dl" | "color";
export type StageGeometryRowType = "new" | "cont" | "empty" | "total";

export interface StageGeometryRow {
  // 워프 좌표계 행 밴드 [y0, y1].
  band: [number, number];
  type: StageGeometryRowType;
  // 실제로 크롭된 행의 세로 박스 [y0, y1]. 크롭 대상이 아니면 null.
  item_box: [number, number] | null;
  // row-{i}.png·crop_ref의 i와 같은 값. 크롭 대상이 아니면 null.
  row_index: number | null;
}

export interface StageGeometry {
  version: number;
  generation: number;
  // EXIF 적용 후 원본 [width, height] — 쿼드 오버레이 viewBox의 입력.
  image_size: [number, number];
  // 워프 결과 [width, height] — 워프 좌표계 패널 viewBox의 입력(상수 하드코딩 금지).
  warp_size: [number, number];
  quad: [number, number][] | null;
  quad_source: StageGeometryQuadSource | null;
  deskew_deg: number | null;
  // 이하는 게이트를 통과한 잡에만 있다. optional로 두는 것이 계약이다 — 강등 잡의
  // 부분 문서에는 키 자체가 없고, null로 좁히면 "검출했는데 0건"과 구분이 사라진다.
  hlines?: number[];
  pitch?: number;
  item_x?: [number, number];
  amount_x?: [number, number];
  rows?: StageGeometryRow[];
}
