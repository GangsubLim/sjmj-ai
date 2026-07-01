import { describe, it, expectTypeOf } from "vitest";
import type {
  CurationJobPair,
  CurationPairPatchResult,
  CurationJobDetail,
  CurationPairPatch,
} from "./curation";

describe("curation 타입 계약", () => {
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
