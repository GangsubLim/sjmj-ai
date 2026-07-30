/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { readFileSync } from "node:fs";

// 프론트 자기 버전 = 루트 VERSION(진실원). backend APP_VERSION과의 동기는
// backend tests/test_version_sync.py가 보장하므로 양쪽이 같은 값을 본다.
const VERSION_PATH = path.resolve(__dirname, "../../../VERSION");
const APP_VERSION = readFileSync(VERSION_PATH, "utf-8").trim();
// 배포 빌드(`npm run build`)는 vitest를 거치지 않으므로 semver 가드가 여기 없으면
// 불량 VERSION 값(예: "v0.5.0", 빈 문자열)이 검증 없이 번들에 그대로 주입된다.
if (!/^\d+\.\d+\.\d+$/.test(APP_VERSION)) {
  throw new Error(`루트 VERSION 형식 불량: "${APP_VERSION}" (${VERSION_PATH})`);
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // 로컬 풀스택: 브라우저(:5173) 동일출처 → /api 를 FastAPI(:8400)로 프록시.
      // cross-origin CORS 회피 + prod 동일출처(backend가 dist+/api 서빙) 구조와 일치.
      "/api": {
        target: "http://127.0.0.1:8400",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/utils/**/*.ts", "src/hooks/**/*.ts", "src/stores/**/*.ts"],
      exclude: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
          ui: ["radix-ui", "cmdk", "class-variance-authority", "clsx", "tailwind-merge"],
        },
      },
    },
  },
});
