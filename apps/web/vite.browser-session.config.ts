import { defineConfig, mergeConfig } from "vitest/config";
import config from "./vite.config";

export default mergeConfig(config, defineConfig({
  define: {
    "import.meta.env.VITE_AUTH_MODE": JSON.stringify("browser"),
    "import.meta.env.VITE_API_BASE_URL": JSON.stringify(""),
  },
  server: {
    host: "127.0.0.1", port: 5173, strictPort: true,
    proxy: {
      "/auth": { target: "http://127.0.0.1:18767", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:18767", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:18767", changeOrigin: true },
    },
  },
}));
