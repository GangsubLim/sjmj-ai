import { isAxiosError } from "axios";
import { CheckIcon, PencilIcon, Trash2Icon } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type {
  HermesKnowledgeChange,
  HermesKnowledgeVersion,
  HermesMismatchField,
  HermesStatus,
} from "@/types/hermes";

// 이 모듈은 **표시만** 소유한다. 상태는 백엔드가 판정한 enum을 그대로 쓰며 여기서
// 재판정하지 않는다 — 판정이 두 벌이면 서버측 status 필터의 결과와 배지가 갈린다(spec §6).
// 커버리지 include가 utils/hooks/stores만이라, 표시 규칙을 컴포넌트에 두면 계상되지 않는다.

export const HERMES_STATUSES: HermesStatus[] = ["deleted", "match", "mismatch"];

export const HERMES_STATUS_LABELS: Record<HermesStatus, string> = {
  deleted: "삭제됨",
  match: "일치",
  mismatch: "불일치",
};

// 라벨에서 유니코드 글리프를 걷어내고 아이콘 세트로 옮긴다 — 글리프는 스크린리더가
// "쓰레기통 삭제됨"으로 읽어 이름을 오염시키고, 앱의 나머지 아이콘(lucide)과도 어긋났다.
export const HERMES_STATUS_ICONS: Record<HermesStatus, LucideIcon> = {
  deleted: Trash2Icon,
  match: CheckIcon,
  mismatch: PencilIcon,
};

// Record로 묶어 HermesStatus에 상태가 추가되면 컴파일 에러로 막는다
// (app/curation/page.tsx의 BADGE_CLASSES와 같은 관례).
// 색은 globals.css의 --success/--warning 토큰을 탄다 — Tailwind 팔레트 직접 사용
// (green-600/amber-600)은 본문 배경 대비 3.0:1로 WCAG AA(4.5:1) 미달이었고, 토큰 밖이라
// .dark에서 대비가 재계산되지도 않았다.
export const HERMES_STATUS_CLASSES: Record<HermesStatus, string> = {
  deleted: "text-muted-foreground",
  match: "text-success",
  // 불일치가 이 화면에서 볼 것이 있는 유일한 상태다 — 볼드로 갈라 눈에 먼저 들어오게 한다.
  mismatch: "font-bold text-warning",
};

export const HERMES_MISMATCH_LABELS: Record<HermesMismatchField, string> = {
  recipient: "수신처",
  item_count: "항목 수",
  name: "품목명",
  supply: "공급가",
  grand_total: "합계",
};

// 초안↔최종 대조 셀 강조 클래스 — 목록(DraftFinal)·상세(CompareLine·ItemCell)가 공유한다.
// 강조 여부는 서버가 준 mismatch_fields로만 판단하고(값 재비교 금지, spec §6), 여기는
// "판정된 불일치를 어떻게 그리는지"만 소유한다.
export const HERMES_HIGHLIGHT_OLD_CLASS = "text-muted-foreground line-through";
export const HERMES_HIGHLIGHT_NEW_CLASS =
  "font-medium text-warning no-underline";

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

// --- 판독 지식 버전 changelog 표시 ---

/** 절별 변경 요약 칩 — "확정 어휘 +1 ↔2" 처럼 절 이름 뒤에 종류별 건수만 붙인다.
 * 종류 기호: + 추가 · − 삭제 · ↔ 그룹 이동 · ~ 값 변경. 건수 0인 종류는 생략한다. */
export function summarizeKnowledgeChanges(
  changes: readonly HermesKnowledgeChange[],
): string[] {
  return changes.map((c) => {
    const parts = [
      c.added.length > 0 ? `+${c.added.length}` : null,
      c.removed.length > 0 ? `−${c.removed.length}` : null,
      c.moved.length > 0 ? `↔${c.moved.length}` : null,
      c.changed.length > 0 ? `~${c.changed.length}` : null,
    ].filter((p): p is string => p !== null);
    return [c.section, ...parts].join(" ");
  });
}

/** 변경 칩 대신 보여줄 한 줄 설명. 변경이 있으면 null(칩이 말한다). 서버의
 * changes=null은 거부·파일 부재·초기 발행 세 뜻이라 missing_file·rejected로 먼저 가른다. */
export function knowledgeVersionNote(
  row: HermesKnowledgeVersion,
): string | null {
  if (row.rejected !== null) return `거부: ${row.rejected}`;
  if (row.missing_file) return "파일 없음";
  if (row.changes === null) return "초기 발행";
  if (row.changes.length === 0) return "변경 없음";
  return null;
}

/** axios 원문(`Request failed with status code 500`)이 한국어 운영 화면에 그대로 뜨는 것을
 * 막는다. axios 에러만 상태코드로 갈라 옮기고, 그 밖의 Error는 message를 보존한다 —
 * 도메인 코드가 직접 던진 한국어 메시지를 일반문으로 덮어쓰면 정보가 줄어든다. */
export function hermesErrorMessage(e: unknown, fallback: string): string {
  if (isAxiosError(e)) {
    const status = e.response?.status;
    if (status === undefined)
      return "서버에 연결하지 못했습니다 — 네트워크를 확인하세요";
    if (status >= 500)
      return "서버가 응답하지 않습니다 — 잠시 후 다시 시도하세요";
    if (status === 404) return "요청한 자료를 찾을 수 없습니다";
    return `요청이 거부되었습니다 (HTTP ${status})`;
  }
  return e instanceof Error ? e.message : fallback;
}
