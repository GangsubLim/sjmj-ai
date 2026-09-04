import { describe, it, expect } from "vitest";

import {
  curationJobBlockedNotice,
  curationJobState,
  isLabelCorrected,
  isLabelRenormalized,
  isPairChanged,
  rowDeltaText,
} from "./curation";

const base = { draft_label: "무", final_label: "무", canonical_label: "무" };

describe("curation 변경 강조 판정", () => {
  it("draft≠final 이면 인식 교정", () => {
    expect(
      isLabelCorrected({ ...base, draft_label: "무우", final_label: "무" }),
    ).toBe(true);
    expect(isLabelCorrected(base)).toBe(false);
  });

  it("final≠canonical 이면 재정규화", () => {
    expect(isLabelRenormalized({ ...base, canonical_label: "배추" })).toBe(
      true,
    );
    expect(isLabelRenormalized(base)).toBe(false);
  });

  it("둘 중 하나라도 변경이면 changed", () => {
    expect(isPairChanged(base)).toBe(false);
    expect(isPairChanged({ ...base, canonical_label: "배추" })).toBe(true);
  });
});

describe("curationJobState 3-state 판별", () => {
  it("한 번도 검수 안 한 잡은 unreviewed", () => {
    expect(
      curationJobState({
        curation_reviewed: false,
        curation_reviewed_at: null,
      }),
    ).toBe("unreviewed");
  });

  it("검수됐다가 해제된 잡은 needs_recheck", () => {
    expect(
      curationJobState({
        curation_reviewed: false,
        curation_reviewed_at: "2026-06-30T08:30:00",
      }),
    ).toBe("needs_recheck");
  });

  it("검수 완료 상태면 reviewed — 첫 검수 시각 유무와 무관하다", () => {
    expect(
      curationJobState({
        curation_reviewed: true,
        curation_reviewed_at: "2026-06-30T08:30:00",
      }),
    ).toBe("reviewed");
    // migration_011 이전에 검수된 잡은 백필이 실패해 시각이 NULL일 수 있다(spec §4.1).
    // 그래도 게이트가 서 있으면 "검수됨"이다 — 판별 순서가 뒤집히면 이 케이스가 깨진다.
    expect(
      curationJobState({
        curation_reviewed: true,
        curation_reviewed_at: null,
      }),
    ).toBe("reviewed");
  });
});

describe("curationJobBlockedNotice 상태별 경고 문구", () => {
  it("done 잡에는 문구가 없다", () => {
    expect(curationJobBlockedNotice("done")).toBeNull();
  });

  it("pending·running은 재처리 대기·진행 문구를 공유한다", () => {
    // 두 상태 모두 "곧 끝난다"가 사실이라 같은 안내로 묶는다.
    expect(curationJobBlockedNotice("pending")).toEqual(
      curationJobBlockedNotice("running"),
    );
    expect(curationJobBlockedNotice("pending")?.title).toBe(
      "⏳ 재처리 대기·진행 중",
    );
  });

  it("failed는 재처리 요청 복구 문구를 쓴다(대기·진행으로 오인시키지 않는다)", () => {
    // 실패 잡에 "처리가 끝난 뒤"는 사실과 다르다(영영 끝나지 않는다) — 백엔드
    // mark_reviewed의 메시지 분기(#93)와 같은 이유로 문구를 가른다.
    const notice = curationJobBlockedNotice("failed");
    expect(notice?.title).toBe("⚠ 처리 실패");
    expect(notice?.body).toContain("재처리를 요청해 다시 시도하세요");
    expect(notice?.body).not.toContain("처리가 끝난 뒤");
  });
});

describe("rowDeltaText", () => {
  it("더한 수와 버린 수를 갈라 표시한다(합산 금지)", () => {
    expect(rowDeltaText({ rows_added: 2, rows_dropped: 1 })).toBe("+2 / −1");
  });

  it("증감 0도 관측된 사실이라 0으로 표시한다", () => {
    expect(rowDeltaText({ rows_added: 0, rows_dropped: 0 })).toBe("+0 / −0");
  });

  it("관측 없음은 대시 하나로 닫는다", () => {
    expect(rowDeltaText({ rows_added: null, rows_dropped: null })).toBe("—");
  });

  it("한쪽만 관측되면 없는 쪽만 물음표로 남긴다", () => {
    expect(rowDeltaText({ rows_added: 3, rows_dropped: null })).toBe("+3 / −?");
  });
});
