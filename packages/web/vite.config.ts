import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// Absolute paths from this file's URL, so `root` and `outDir` do not depend on the cwd (no Node types needed).
const here = (rel: string): string => decodeURIComponent(new URL(rel, import.meta.url).pathname);

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, here("."), "WYND_");
  return {
    root: here("."),
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      // `wynd serve-api` default; SSE streams pass through the proxy unbuffered.
      proxy: { "/api": { target: env.WYND_API_URL ?? "http://127.0.0.1:8780", changeOrigin: false } },
    },
    build: {
      outDir: here("../controller/src/wynd/controller/web_dist"),
      emptyOutDir: true,
      sourcemap: true,
      target: "es2022",
    },
    test: {
      environment: "jsdom",
      setupFiles: ["src/test/setup.ts"],
      css: false,
      restoreMocks: true,
    },
  };
});
