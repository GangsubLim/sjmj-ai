import type { HermesMismatchField, HermesStatus } from "@/types/hermes";

// 이 모듈은 **표시만** 소유한다. 상태는 백엔드가 판정한 enum을 그대로 쓰며 여기서
// 재판정하지 않는다 — 판정이 두 벌이면 서버측 status 필터의 결과와 배지가 갈린다(spec §6).
// 커버리지 include가 utils/hooks/stores만이라, 표시 규칙을 컴포넌트에 두면 계상되지 않는다.

export const HERMES_STATUSES: HermesStatus[] = ["deleted", "match", "mismatch"];

export const HERMES_STATUS_LABELS: Record<HermesStatus, string> = {
  deleted: "🗑 삭제됨",
  match: "✓ 일치",
  mismatch: "✎ 불일치",
};

// Record로 묶어 HermesStatus에 상태가 추가되면 컴파일 에러로 막는다
// (app/curation/page.tsx의 BADGE_CLASSES와 같은 관례).
export const HERMES_STATUS_CLASSES: Record<HermesStatus, string> = {
  deleted: "text-muted-foreground",
  match: "text-green-600",
  // 불일치가 이 화면에서 볼 것이 있는 유일한 상태다 — 볼드로 갈라 눈에 먼저 들어오게 한다.
  mismatch: "font-bold text-amber-600",
};

export const HERMES_MISMATCH_LABELS: Record<HermesMismatchField, string> = {
  recipient: "수신처",
  item_count: "항목 수",
  name: "품목명",
  supply: "공급가",
  grand_total: "합계",
};

/** 목록·상세가 공유하는 상태 필터 URL 파라미터 이름. */
export const HERMES_STATUS_PARAM = "status";

/** 선언된 3종만 필터로 인정한다 — 다른 철자는 필터 없음으로 접어, 서버가 400을 내는
 * 값을 프론트가 만들어 보내지 않게 한다(대소문자도 구분한다: 표기를 하나로 고정). */
export function parseHermesStatus(raw: string | null): HermesStatus | null {
  return HERMES_STATUSES.includes(raw as HermesStatus)
    ? (raw as HermesStatus)
    : null;
}

/** 일치율 표시. 서버는 분모 0에서도 0.0을 주므로(agent_report._rate와 같은 규약)
 * 분모를 함께 받아 — 로 접는다. 0.0%로 그리면 '전부 틀렸다'로 읽힌다. */
export function formatRate(rate: number, denominator: number): string {
  return denominator === 0 ? "—" : `${(rate * 100).toFixed(1)}%`;
}

/** 금액 표시 — 값 없음(null)과 0을 구분한다. */
export function formatAmount(v: number | null): string {
  return v === null ? "—" : v.toLocaleString("ko-KR");
}

// page=1·필터 없음이면 쿼리를 붙이지 않는다 — "1페이지에서 진입"과 "쿼리 없이 북마크"가
// 같은 주소가 된다(lib/curation-url의 URL 계약과 같은 관례).
function query(page: number, status: HermesStatus | null): string {
  const params = new URLSearchParams();
  if (page > 1) params.set("page", String(page));
  if (status) params.set(HERMES_STATUS_PARAM, status);
  const search = params.toString();
  return search ? `?${search}` : "";
}

export function hermesListUrl(
  page: number,
  status: HermesStatus | null,
): string {
  return `/hermes${query(page, status)}`;
}

/** 상세 URL. page·status는 "이 건이 속한 목록 상태"이며 목록 복귀에 쓰인다. */
export function hermesEntryUrl(
  id: number,
  page: number,
  status: HermesStatus | null,
): string {
  return `/hermes/${id}${query(page, status)}`;
}
