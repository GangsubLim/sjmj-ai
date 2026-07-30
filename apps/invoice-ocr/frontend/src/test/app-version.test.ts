import { readFileSync } from "node:fs";
import path from "node:path";

// 검증 대상은 `vite.config.ts`의 define이다(코로케이트할 소스가 없어 src/test/에 둔다).
// 루트 VERSION(SSOT)을 테스트가 직접 읽어 대조하므로, define이 엉뚱한 값을 주입하거나
// 엉뚱한 파일을 읽으면 여기서 깨진다. backend tests/test_version_sync.py와 대칭.
// 주의: vitest는 ESM이라 __dirname이 없다 — import.meta.dirname을 쓴다(실측 확인).
// 상대경로 5단계 유래: test → src → frontend → invoice-ocr → apps → repo.
const ROOT_VERSION = readFileSync(
  path.resolve(import.meta.dirname, "../../../../../VERSION"),
  "utf-8",
).trim();

describe("__APP_VERSION__ 주입", () => {
  it("루트 VERSION과 정확히 일치한다", () => {
    expect(__APP_VERSION__).toBe(ROOT_VERSION);
  });

  it("semver 문자열이다", () => {
    expect(typeof __APP_VERSION__).toBe("string");
    expect(__APP_VERSION__).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
