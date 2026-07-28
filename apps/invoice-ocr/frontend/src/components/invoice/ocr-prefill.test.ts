import { describe, it, expect } from "vitest";
import { rowsToItems, rowsToOcrMeta } from "./ocr-prefill";
import type { OcrResult } from "@/types/ocr";

describe("rowsToItems", () => {
  it("maps top-1 label to name and supply to unit_price, carrying crop_ref", () => {
    const items = rowsToItems({
      rows: [
        {
          row_index: 0,
          crop_ref: "job-42/row-0",
          item_top5: [{ label: "삼겹살", sim: 0.8 }],
          supply: 120000,
          amount_raw: "120,000",
        },
      ],
      supply_sum: 120000,
      warp_ok: true,
    });
    expect(items[0].name).toBe("삼겹살");
    expect(items[0].unit_price).toBe(120000);
    expect(items[0].crop_ref).toBe("job-42/row-0");
  });

  it("empty top5 yields blank name for manual typing", () => {
    const items = rowsToItems({
      rows: [
        {
          row_index: 0,
          crop_ref: "job-1/row-0",
          item_top5: [],
          supply: null,
          amount_raw: "",
        },
      ],
      supply_sum: 0,
      warp_ok: true,
    });
    expect(items[0].name).toBe("");
    expect(items[0].unit_price).toBe(0);
  });
});

function result(rows: OcrResult["rows"]): OcrResult {
  // 0.75는 확정 임계(T3 산정, docs/work/2026-07/2026-07-28-ocr-candidate-selection/threshold.md
  // — git 비추적, 없으면 Issue #22 참조)와 맞추려는 것이 아니라, rowsToOcrMeta가 이 값을
  // 읽지 않아 테스트 결과에 영향이 없음을 밝히기 위한 임의값이다.
  return { rows, supply_sum: 0, warp_ok: true, item_conf_threshold: 0.85 };
}

const ROW = {
  row_index: 2,
  crop_ref: "job-7/row-2",
  item_top5: [
    { label: "타이어", sim: 0.72 },
    { label: "튜브", sim: 0.68 },
  ],
  supply: 85000,
  amount_raw: "85",
};

describe("rowsToOcrMeta", () => {
  it("crop_ref를 키로 후보 전체를 보존한다", () => {
    const meta = rowsToOcrMeta(result([{ ...ROW, item_uncertain: true }]), 7);
    expect(meta.get("job-7/row-2")!.candidates).toEqual(ROW.item_top5);
    expect(meta.get("job-7/row-2")!.rowIndex).toBe(2);
    expect(meta.get("job-7/row-2")!.jobId).toBe(7);
  });

  it("item_uncertain을 그대로 매핑한다", () => {
    expect(
      rowsToOcrMeta(result([{ ...ROW, item_uncertain: true }]), 7).get(
        "job-7/row-2",
      )!.uncertain,
    ).toBe(true);
    expect(
      rowsToOcrMeta(result([{ ...ROW, item_uncertain: false }]), 7).get(
        "job-7/row-2",
      )!.uncertain,
    ).toBe(false);
  });

  it("플래그가 없는 과거 잡은 확신으로 본다", () => {
    expect(rowsToOcrMeta(result([ROW]), 7).get("job-7/row-2")!.uncertain).toBe(
      false,
    );
  });

  it("후보가 없는 행도 빈 배열로 항목을 남긴다", () => {
    const meta = rowsToOcrMeta(
      result([{ ...ROW, item_top5: [], item_uncertain: true }]),
      7,
    );
    expect(meta.get("job-7/row-2")!.candidates).toEqual([]);
  });

  it("rowsToItems는 기존 동작(top1만 추출)을 유지한다", () => {
    const items = rowsToItems(result([{ ...ROW, item_uncertain: true }]));
    expect(items[0].name).toBe("타이어");
    expect(items[0].crop_ref).toBe("job-7/row-2");
    expect(items[0]).not.toHaveProperty("item_uncertain");
  });

  it("여러 행을 각자의 crop_ref 키로 흩는다", () => {
    const row3 = {
      row_index: 3,
      crop_ref: "job-7/row-3",
      item_top5: [{ label: "브레이크패드", sim: 0.9 }],
      supply: 45000,
      amount_raw: "45",
      item_uncertain: false,
    };
    const meta = rowsToOcrMeta(
      result([{ ...ROW, item_uncertain: true }, row3]),
      7,
    );

    expect(meta.size).toBe(2);
    expect(meta.get("job-7/row-2")!.rowIndex).toBe(2);
    expect(meta.get("job-7/row-2")!.uncertain).toBe(true);
    expect(meta.get("job-7/row-3")!.rowIndex).toBe(3);
    expect(meta.get("job-7/row-3")!.uncertain).toBe(false);
  });

  it("빈 rows는 빈 맵을 반환한다", () => {
    expect(rowsToOcrMeta(result([]), 7).size).toBe(0);
  });
});
