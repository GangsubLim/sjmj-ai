/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
