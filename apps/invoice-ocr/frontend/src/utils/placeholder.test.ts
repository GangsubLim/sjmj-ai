import { describe, it, expect } from "vitest";
import type { SyntheticEvent } from "react";

import { placeholderSvg, fallbackToPlaceholder } from "./placeholder";

describe("placeholderSvg", () => {
  it("치수를 담은 svg data URI를 만든다", () => {
    const uri = placeholderSvg(64, 40);
    expect(uri.startsWith("data:image/svg+xml")).toBe(true);
    const decoded = decodeURIComponent(uri);
    expect(decoded).toContain('width="64"');
    expect(decoded).toContain('height="40"');
  });
});

describe("fallbackToPlaceholder", () => {
  it("src를 placeholder로 교체한다", () => {
    const ph = placeholderSvg(10, 10);
    const img = { src: "http://x/img.png" } as unknown as HTMLImageElement;
    const handler = fallbackToPlaceholder(ph);

    handler({
      currentTarget: img,
    } as unknown as SyntheticEvent<HTMLImageElement>);

    expect(img.src).toBe(ph);
  });

  it("이미 placeholder면 재설정하지 않는다(재진입 가드)", () => {
    const ph = placeholderSvg(10, 10);
    let writes = 0;
    const img = {
      _src: ph,
      get src() {
        return this._src;
      },
      set src(v: string) {
        writes += 1;
        this._src = v;
      },
    };
    const handler = fallbackToPlaceholder(ph);

    handler({
      currentTarget: img,
    } as unknown as SyntheticEvent<HTMLImageElement>);

    expect(writes).toBe(0);
  });
});
