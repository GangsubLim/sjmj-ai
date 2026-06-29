import { describe, it, expect } from "vitest";
import { rowsToItems } from "./ocr-prefill";

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
