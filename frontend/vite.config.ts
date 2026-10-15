import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built assets are served by Django staticfiles from peon/static/dist/ and
// resolved through manifest.json by django-vite.
export default defineConfig({
  base: "/static/dist/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: path.resolve(__dirname, "../peon/static/dist"),
    emptyOutDir: true,
    manifest: "manifest.json",
    sourcemap: true,
    // CodeMirror is one lazily loaded ~640 kB chunk by design.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      input: {
        base: path.resolve(__dirname, "src/entries/base.ts"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
