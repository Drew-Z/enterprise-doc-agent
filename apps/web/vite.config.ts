import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // The upload hash worker is lazy-loaded after file selection. Pre-bundle its
  // dependency at startup so a cold dev server does not optimize it mid-test
  // and reload the page while hashing is in progress.
  optimizeDeps: {
    include: ["hash-wasm"],
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/health": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
