// api-spec(.claude/ai-context/api-spec.json)의 Hermes* 스키마를 미러한다.
// 드리프트 시 api-spec이 SSoT.

export type HermesStatus = "deleted" | "match" | "mismatch";

export type HermesMismatchField =
  "recipient" | "item_count" | "name" | "supply" | "grand_total";

/** agent_report.summarize의 13키 중 edited를 뺀 12키. edited는 타임스탬프 파생이라
 * 품목 교정(부모 행 무변경)을 통째로 놓쳐 서버가 응답에서 뺀다. */
export interface HermesTotals {
  count: number;
  missing: number;
  pairs: number;
  name_hits: number;
  supply_hits: number;
  untouched: number;
  // 분모 0이면 서버가 0.0을 준다 — 화면은 formatRate가 — 로 접는다.
  recipient_rate: number;
  item_count_rate: number;
  /** 분모는 count가 아니라 pairs. */
  name_rate: number;
  /** 분모는 count가 아니라 pairs. */
  supply_rate: number;
  grand_total_rate: number;
  untouched_rate: number;
}

export interface HermesKnowledge {
  /** 거부 기록을 제외한 최신 발행 버전. 발행 이력이 없으면 0. */
  version: number;
  published_at: string | null;
  corrections: number;
}

/** 지식 버전 구간별 집계. version 0은 '발행 이전' 구간이다. */
export interface HermesVersionRow extends HermesTotals {
  version: number;
}

export interface HermesSummary {
  totals: HermesTotals;
  knowledge: HermesKnowledge;
  by_version: HermesVersionRow[];
}

export interface HermesEntrySummary {
  id: number;
  issue_date_draft: string | null;
  issue_date_final: string | null;
  recipient_draft: string | null;
  recipient_final: string | null;
  item_count_draft: number;
  item_count_final: number | null;
  grand_total_draft: number | null;
  grand_total_final: number | null;
  // 판정 주체는 백엔드 단독이다 — 프론트가 다시 판정하면 필터 결과와 배지가 갈린다.
  status: HermesStatus;
  mismatch_fields: HermesMismatchField[];
  has_photo: boolean;
  /** raw.json 존재. false면 화면이 전사값 열 자체를 숨긴다. */
  has_raw: boolean;
}

/** 행 1개의 6열. 짝이 없는 쪽은 null. */
export interface HermesRowCell {
  name: string | null;
  quantity: number | null;
  unit: string | null;
  unit_price: number | null;
  supply: number | null;
  deduction: boolean;
}

export interface HermesRow {
  index: number;
  /** 1단계 패스1 전사값. raw.json 부재면 null. */
  raw: { text: string | null; conf: string | null } | null;
  // 길이는 max(초안 행, 최종본 행) — 사람이 추가한 행은 draft가, 지운 행은 final이 null이다.
  draft: HermesRowCell | null;
  final: HermesRowCell | null;
  mismatch: ("name" | "supply")[];
}

/** 최종본 invoices 행 중 화면이 쓰는 열(서버는 SELECT *라 이보다 많이 준다). */
export interface HermesInvoiceHeader {
  id: number;
  issue_date: string;
  recipient: string;
  grand_total: number;
}

/** 초안 원문 중 화면이 쓰는 키(POST /api/invoices 바디 그대로라 이보다 많이 온다). */
export interface HermesDraftBody {
  issue_date?: string;
  recipient?: string;
  grand_total?: number;
}

export interface HermesEntryDetail {
  id: number;
  status: HermesStatus;
  draft: HermesDraftBody;
  final: HermesInvoiceHeader | null;
  rows: HermesRow[];
  has_photo: boolean;
  // 판정 주체는 백엔드 단독이다 — 프론트가 값 비교로 재도출하면 목록과 갈린다.
  mismatch_fields: HermesMismatchField[];
}

// --- 판독 지식 버전 changelog (GET /api/hermes/knowledge/versions) ---

/** 절 안의 항목 1개. group은 등급명·소제목 같은 그룹 라벨이며 없으면 "". */
export interface HermesKnowledgeItem {
  group: string;
  text: string;
}

/** 같은 텍스트가 같은 절의 다른 그룹으로 이동(어휘 등급 이동이 전형). */
export interface HermesKnowledgeMove {
  text: string;
  from: string;
  to: string;
}

/** 같은 그룹·같은 키의 값 변경. '라벨: 값' 꼴은 라벨이 키, 서술문은 근거 (#…)를 뗀
 * 본문이 키이고 근거가 값이다. */
export interface HermesKnowledgeValueChange {
  group: string;
  key: string;
  before: string;
  after: string;
}

/** 절 1개의 변경. 변경이 있는 절만 온다. */
export interface HermesKnowledgeChange {
  section: string;
  added: HermesKnowledgeItem[];
  removed: HermesKnowledgeItem[];
  moved: HermesKnowledgeMove[];
  changed: HermesKnowledgeValueChange[];
}

export interface HermesKnowledgeVersion {
  version: number;
  published_at: string;
  /** 거부 기록은 null. */
  corrections_through: number | null;
  /** 발행 거부 사유. 발행 기록은 null. */
  rejected: string | null;
  /** 직전 발행 대비 변경. null은 초기 발행·거부·파일 부재(missing_file), 빈 배열은
   * 내용 동일 재발행. 판정 주체는 백엔드 단독이다 — 프론트는 md를 받지 않는다. */
  changes: HermesKnowledgeChange[] | null;
  missing_file: boolean;
}
