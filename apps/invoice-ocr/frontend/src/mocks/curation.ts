import type { CurationJobDetail } from "@/types/curation";

// 인메모리 정본: 잡 상세 배열. 요약/목록은 여기서 파생한다.
// 주의: e2e(curation.spec.ts)가 #128의 row_index 0·1 존재에 의존한다. 행 추가/삭제 시 e2e 셀렉터 동기.
export const mockCurationJobDetails: CurationJobDetail[] = [
  {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
    // 한 번도 검수 안 한 잡 — curationJobState가 "unreviewed"로 판별한다.
    curation_reviewed_at: null,
    warp_ok: true,
    created_at: "2026-06-30T09:10:00",
    // 잡마다 다른 토큰 — 전부 같으면 교차 잡 토큰 누수(옛 잡 응답이 새 잡 토큰을 덮는
    // 회귀)가 mock에서 전혀 드러나지 않는다. 값은 서버의 UNIX_TIMESTAMP 형태를 흉내 낸다.
    job_token: "1780000010",
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
        crop_available: true,
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
        crop_available: true,
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
    // 검수 완료 잡. e2e(curation.spec.ts)가 이 잡의 쌍을 수정해 상세의
    // "↺ 재검수 필요" 배너와 목록의 같은 이름 뱃지를 각각 확인한다 — 서버의
    // mark_reviewed가 쌍 스탬프와 같은 시각을 찍으므로 pairs의 reviewed_at과 맞춘다.
    curation_reviewed_at: "2026-06-30T08:30:00",
    warp_ok: false,
    created_at: "2026-06-30T08:00:00",
    job_token: "1780000020",
    pairs: [
      {
        id: 8001,
        crop_ref: "127/0",
        row_index: 0,
        draft_label: "당근",
        final_label: "당근",
        canonical_label: "당근",
        supply: 5000,
        // §6 세 번째 칸(기계가 배제했으나 사람이 되돌림)을 mock 모드에서 재현하는 자리.
        // status가 "included"라 목록에서 "제외" 버튼으로 노출되는 유일한 pair —
        // e2e(curation.spec.ts)가 "제외" 버튼 .first()로 이 pair를 클릭해 게이트를
        // 해제하고 "↺ 재검수 필요" 배너를 확인한다. status를 바꾸면 그 셀렉터가 어긋난다.
        status: "included",
        exclusion_reason: "blank_crop",
        reviewed_at: "2026-06-30T08:30:00",
        uncertain: false,
        crop_available: true,
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
        // status가 이미 "excluded"라 "포함" 버튼으로 노출되므로 e2e의 "제외" 버튼
        // .first() 클릭(pair 8001) 대상이 아니다 — 값을 바꾸면 그 셀렉터가 어긋난다.
        status: "excluded",
        exclusion_reason: "blank_crop",
        // 잡 127은 curation_reviewed=true다. 서버의 mark_reviewed는 같은 트랜잭션에서
        // 미스탬프 쌍 전부에 reviewed_at을 찍으므로(검수완료 + reviewed_at=null 조합은
        // 서버가 만들 수 없다) 잡과 같은 시각을 준다 — 안 그러면 mock 큐 화면이
        // "검수완료인데 미처리 1건"이라는 유령 상태를 보여준다.
        reviewed_at: "2026-06-30T08:30:00",
        uncertain: false,
        crop_available: true,
        top5: [],
      },
    ],
  },
  {
    // "재검수 필요"(curation_reviewed=false + curation_reviewed_at≠null)를 정적으로
    // 재현하는 자리 — 이 상태는 런타임 PATCH로만 만들어지고 새로고침이면 시드로
    // 리셋되므로, 이 잡이 없으면 mock 모드 수동 QA·디자인 리뷰가 ↺ 뱃지를 볼 수 없다.
    // e2e는 이 잡을 건드리지 않는다(미검수 배지·"제외" .first()는 #128/#127 것이고,
    // 목록 뱃지 단언은 행 스코프다). 시드를 늘릴 때 그 전제를 다시 확인할 것.
    job_id: 126,
    invoice_id: 339,
    curation_reviewed: false,
    curation_reviewed_at: "2026-06-29T17:20:00",
    warp_ok: true,
    created_at: "2026-06-29T17:00:00",
    job_token: "1780000030",
    pairs: [
      {
        // 검수 후 다시 손댄 쌍 — 서버가 같은 UPDATE에서 reviewed_at을 NULL로 되돌린다.
        id: 7001,
        crop_ref: "126/0",
        row_index: 0,
        draft_label: "대파",
        final_label: "대파",
        canonical_label: "대파",
        supply: 9000,
        status: "included",
        exclusion_reason: null,
        reviewed_at: null,
        uncertain: false,
        crop_available: true,
        top5: [{ label: "대파", sim: 0.86 }],
      },
      {
        // 첫 검수 때 스탬프된 채 남은 쌍 — 잡 스탬프와 같은 시각(mark_reviewed 단일 tx).
        id: 7002,
        crop_ref: "126/1",
        row_index: 1,
        draft_label: "양파",
        final_label: "양파",
        canonical_label: "양파",
        supply: 4000,
        status: "included",
        exclusion_reason: null,
        reviewed_at: "2026-06-29T17:20:00",
        uncertain: false,
        crop_available: true,
        top5: [{ label: "양파", sim: 0.93 }],
      },
      {
        // 재처리 승계에 실패한 미결 쌍 — 시드에 하나도 없으면 "그림 없음" 분기와
        // 승계 실패 배지가 mock QA·디자인 리뷰에서 한 번도 렌더되지 않는다.
        // row_index는 옛 세대 값 그대로다(승계 실패 쌍은 갱신 대상에서 빠진다).
        id: 7003,
        crop_ref: "job-126/orphan-7003",
        row_index: 2,
        draft_label: "감자",
        final_label: "감자",
        canonical_label: "감자",
        supply: 6000,
        status: "excluded",
        exclusion_reason: "relink_failed",
        reviewed_at: null,
        uncertain: false,
        crop_available: false,
        top5: [],
      },
    ],
  },
];
