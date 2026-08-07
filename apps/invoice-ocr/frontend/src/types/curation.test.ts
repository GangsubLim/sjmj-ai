// 이 파일의 expectTypeOf 단언은 런타임(vitest)이 아니라 컴파일 타임(`npm run build`의
// tsc -b)이 실효 게이트다 — vitest 자체는 타입 오류를 잡지 않으므로 값 비교(`expect(...).toBe(...)`)
// 만으로는 필드를 optional로 되돌려도 항상 GREEN이다. 다음에 이 파일을 만지는 사람은
// 반드시 tsc -b로도 확인할 것.
import { describe, it, expect, expectTypeOf } from "vitest";
import type {
  CurationJobPair,
  CurationPairBase,
  CurationPairPatchResult,
  CurationJobDetail,
  CurationJobSummary,
  CurationPairPatch,
  CurationPairPatchBody,
} from "./curation";

describe("curation 타입 계약", () => {
  it("pair 타입이 자동 배제 사유를 담는다", () => {
    const pair: CurationJobPair = {
      id: 1,
      crop_ref: "job-1/row-0",
      row_index: 0,
      draft_label: null,
      final_label: null,
      canonical_label: null,
      supply: null,
      status: "excluded",
      exclusion_reason: "blank_crop",
      reviewed_at: null,
      uncertain: false,
      crop_available: true,
      top5: [],
    };
    expect(pair.exclusion_reason).toBe("blank_crop");
  });

  it("exclusion_reason은 필수이며 blank_crop|relink_failed|null만 허용한다(optional 회귀 차단)", () => {
    // 인덱스 접근 타입은 optional 필드면 `| undefined`가 섞인다 — exclusion_reason이
    // `exclusion_reason?:`로 되돌아가면 이 단언이 tsc -b에서 깨진다.
    expectTypeOf<CurationPairBase["exclusion_reason"]>().toEqualTypeOf<
      "blank_crop" | "relink_failed" | null
    >();
  });

  it("curation_reviewed_at·job_curation_reviewed는 필수다(optional 회귀 차단)", () => {
    // toHaveProperty는 optional 필드에도 통과한다 — toEqualTypeOf로 `| undefined`
    // 섞임까지 잡아야 실제로 optional 회귀를 차단한다.
    expectTypeOf<CurationJobSummary["curation_reviewed_at"]>().toEqualTypeOf<
      string | null
    >();
    expectTypeOf<CurationJobDetail["curation_reviewed_at"]>().toEqualTypeOf<
      string | null
    >();
    expectTypeOf<
      CurationPairPatchResult["job_curation_reviewed"]
    >().toEqualTypeOf<boolean>();
  });

  it("잡 상세 pair는 top5를 가지고 job_id·잡 게이트는 없다", () => {
    expectTypeOf<CurationJobPair>().toHaveProperty("top5");
    expectTypeOf<CurationJobPair>().not.toHaveProperty("job_id");
    // 계약 비대칭 — 게이트는 PATCH 응답 전용이다(api-spec의 공유 CurationPair와 달리
    // TS 타입은 갈라 둔다).
    expectTypeOf<CurationJobPair>().not.toHaveProperty("job_curation_reviewed");
  });

  it("PATCH 결과는 job_id·잡 게이트를 가지고 top5는 없다", () => {
    expectTypeOf<CurationPairPatchResult>().toHaveProperty("job_id");
    expectTypeOf<CurationPairPatchResult>().toHaveProperty(
      "job_curation_reviewed",
    );
    expectTypeOf<CurationPairPatchResult>().not.toHaveProperty("top5");
  });

  it("잡 상세는 pairs 배열을 가진다", () => {
    expectTypeOf<CurationJobDetail["pairs"]>().toEqualTypeOf<
      CurationJobPair[]
    >();
  });

  it("컴포넌트가 만드는 PATCH는 status·canonical_label만 갖는다", () => {
    // job_token은 훅이 채운다 — 컴포넌트 쪽 호출부가 이 필드 없이 컴파일돼야 한다.
    expectTypeOf<CurationPairPatch>().toEqualTypeOf<{
      status?: "included" | "excluded";
      canonical_label?: string;
    }>();
  });

  it("와이어에 나가는 PATCH 본문은 job_token을 필수로 요구한다", () => {
    // 서버가 필수로 요구하므로(spec §12) optional로 두면 훅이 토큰을 못 채운 창에서
    // axios가 키를 떨궈 의도한 409 대신 400이 나간다 — 방어를 타입으로 강제한다.
    // optional이면 이 타입이 `string | undefined`가 되어 단언이 깨진다 — 필수임의 증명이다.
    expectTypeOf<CurationPairPatchBody["job_token"]>().toEqualTypeOf<string>();
    expectTypeOf<CurationPairPatchBody["canonical_label"]>().toEqualTypeOf<
      string | undefined
    >();
  });
});
