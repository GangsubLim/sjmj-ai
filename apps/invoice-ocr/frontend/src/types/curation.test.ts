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
  CurationPairPatch,
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
      top5: [],
    };
    expect(pair.exclusion_reason).toBe("blank_crop");
  });

  it("exclusion_reason은 필수이며 blank_crop|null만 허용한다(optional 회귀 차단)", () => {
    // 인덱스 접근 타입은 optional 필드면 `| undefined`가 섞인다 — exclusion_reason이
    // `exclusion_reason?:`로 되돌아가면 이 단언이 tsc -b에서 깨진다.
    expectTypeOf<CurationPairBase["exclusion_reason"]>().toEqualTypeOf<
      "blank_crop" | null
    >();
  });

  it("잡 상세 pair는 top5를 가지고 job_id는 없다", () => {
    expectTypeOf<CurationJobPair>().toHaveProperty("top5");
    expectTypeOf<CurationJobPair>().not.toHaveProperty("job_id");
  });

  it("PATCH 결과는 job_id를 가지고 top5는 없다", () => {
    expectTypeOf<CurationPairPatchResult>().toHaveProperty("job_id");
    expectTypeOf<CurationPairPatchResult>().not.toHaveProperty("top5");
  });

  it("잡 상세는 pairs 배열을 가진다", () => {
    expectTypeOf<CurationJobDetail["pairs"]>().toEqualTypeOf<
      CurationJobPair[]
    >();
  });

  it("PATCH 본문은 status·canonical_label 모두 선택적이다", () => {
    expectTypeOf<CurationPairPatch>().toEqualTypeOf<{
      status?: "included" | "excluded";
      canonical_label?: string;
    }>();
  });
});
