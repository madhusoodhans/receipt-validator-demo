import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into ../web, which app/server.py serves on :8601: index.html at /, hashed bundles at /assets/*.
// The dev server proxies /api and /health to the mock server on :8603 by default.
// API=live proxies to the real backend on :8601 instead; API_TARGET=<url> picks any other origin.
const target = process.env.API_TARGET || (process.env.API === "live" ? "http://127.0.0.1:8601" : "http://127.0.0.1:8603");

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../web", emptyOutDir: true, assetsDir: "assets", chunkSizeWarningLimit: 900 },
  server: {
    port: 5174,
    host: "127.0.0.1",
    proxy: { "/api": { target, changeOrigin: true }, "/health": { target, changeOrigin: true } },
  },
});
