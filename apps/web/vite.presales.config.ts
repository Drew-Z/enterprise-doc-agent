import { defineConfig, mergeConfig } from "vitest/config";
import config from "./vite.config";

export default mergeConfig(config, defineConfig({
  define: { "import.meta.env.VITE_AUTH_MODE": JSON.stringify("bearer") },
  server: {
    host: "127.0.0.1", port: 18073, strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:18765", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:18765", changeOrigin: true },
    },
  },
}));
