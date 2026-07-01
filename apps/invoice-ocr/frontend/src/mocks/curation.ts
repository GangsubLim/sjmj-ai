import type { CurationJobDetail } from "@/types/curation";

// 인메모리 정본: 잡 상세 배열. 요약/목록은 여기서 파생한다.
// 주의: e2e(curation.spec.ts)가 #128의 row_index 0·1 존재에 의존한다. 행 추가/삭제 시 e2e 셀렉터 동기.
export const mockCurationJobDetails: CurationJobDetail[] = [
  {
    job_id: 128,
    invoice_id: 341,
    curation_reviewed: false,
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
        reviewed_at: null,
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
        status: "included",
        reviewed_at: null,
        top5: [
          { label: "무", sim: 0.77 },
          { label: "배추", sim: 0.21 },
        ],
      },
    ],
  },
  {
    job_id: 127,
    invoice_id: 340,
    curation_reviewed: true,
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
        status: "included",
        reviewed_at: "2026-06-30T08:30:00",
        top5: [{ label: "당근", sim: 0.88 }],
      },
    ],
  },
];
