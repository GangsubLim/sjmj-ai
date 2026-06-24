import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // dev: 브라우저(:5173) 동일출처 → /api 를 FastAPI(:8400)로 프록시.
      "/api": {
        target: "http://127.0.0.1:8400",
        changeOrigin: true,
      },
    },
  },
});
