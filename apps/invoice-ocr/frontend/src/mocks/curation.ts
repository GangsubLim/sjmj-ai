import type { CurationJobDetail } from "@/types/curation";

// 인메모리 정본: 잡 상세 배열. 요약/목록은 여기서 파생한다.
// 주의: e2e(curation.spec.ts)가 #128의 row_index 0·1 존재에 의존한다. 행 추가/삭제 시 e2e 셀렉터 동기.
export const mockCurationJobDetails: CurationJobDetail[] = [
  {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    curation_reviewed_at: null,
    warp_ok: true,
    created_at: "2026-06-30T09:10:00",
    pairs: [
      {
        id: 9001,
        crop_ref: "128/0",
        row_index: 0,
        draft_label: "배추",
        final_label: "배추",
        canonical_label: "배추",
        supply: 12000,
        status: "included",
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
        top5: [
          { label: "배추", sim: 0.91 },
          { label: "무", sim: 0.42 },
          { label: "파", sim: 0.31 },
        ],
      },
      {
        id: 9002,
        crop_ref: "128/1",
        row_index: 1,
        draft_label: "무우",
        final_label: "무",
        canonical_label: "무",
        supply: 8000,
        // status를 바꾸지 않는다. e2e(curation.spec.ts:26-30)가 이 잡에서 "제외"→"포함"
        // 토글을 단언하는데, 시드가 이미 excluded면 그 단언이 클릭 전부터 참이 되어
        // 조용히 무력화된다(e2e는 playwright.config.ts webServer가 VITE_USE_MOCK=true로 띄운다).
        status: "included",
        exclusion_reason: null,
        reviewed_at: null,
        // mock 모드에서도 미확신 배지가 한 번은 재현되도록 이 pair만 미확신으로 둔다.
        // 서버 값은 ITEM_CONF_THRESHOLD(0.75) 기준 top1 sim 파생이므로 sim도 함께
        // 임계 아래로 낮춘다 — 안 그러면 서버가 만들 수 없는 조합이 된다.
        uncertain: true,
        top5: [
          { label: "무", sim: 0.62 },
          { label: "배추", sim: 0.21 },
        ],
      },
    ],
  },
  {
    job_id: 127,
    invoice_id: 340,
    curation_reviewed: true,
    curation_reviewed_at: "2026-06-30T08:30:00",
    warp_ok: false,
    created_at: "2026-06-30T08:00:00",
    pairs: [
      {
        id: 8001,
        crop_ref: "127/0",
        row_index: 0,
        draft_label: "당근",
        final_label: "당근",
        canonical_label: "당근",
        supply: 5000,
        // job 127은 e2e가 워프 placeholder만 확인하므로 상호작용 단언에 영향이 없다.
        // §6 세 번째 칸(기계가 배제했으나 사람이 되돌림)을 mock 모드에서 재현하는 자리.
        status: "included",
        exclusion_reason: "blank_crop",
        reviewed_at: "2026-06-30T08:30:00",
        uncertain: false,
        top5: [{ label: "당근", sim: 0.88 }],
      },
      {
        id: 8002,
        crop_ref: "127/1",
        row_index: 1,
        draft_label: null,
        final_label: null,
        canonical_label: null,
        supply: null,
        // §6 두 번째 칸(기계 배제, 사람 미검토) — 운영 다수·배지 기본 표시 상태를 mock에도 재현.
        // job 127은 e2e가 워프 placeholder만 확인하므로(curation.spec.ts) 추가해도 안전.
        status: "excluded",
        exclusion_reason: "blank_crop",
        // 잡 127은 curation_reviewed=true다. 서버의 mark_reviewed는 같은 트랜잭션에서
        // 미스탬프 쌍 전부에 reviewed_at을 찍으므로(검수완료 + reviewed_at=null 조합은
        // 서버가 만들 수 없다) 잡과 같은 시각을 준다 — 안 그러면 mock 큐 화면이
        // "검수완료인데 미처리 1건"이라는 유령 상태를 보여준다.
        reviewed_at: "2026-06-30T08:30:00",
        uncertain: false,
        top5: [],
      },
    ],
  },
];
