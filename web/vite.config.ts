import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";

// GitHub Pages serves this repo at https://kinjalgoswami68.github.io/-SentinelAI/
// Pages: landing at /, the app at /app/ and the public demo at /app/demo/.
export default defineConfig({
  base: process.env.PAGES_BASE ?? "/-SentinelAI/",
  plugins: [react(), tailwindcss()],
  build: {
    target: "es2022",
    rollupOptions: {
      input: {
        landing: resolve(import.meta.dirname, "index.html"),
        app: resolve(import.meta.dirname, "app/index.html"),
      },
    },
  },
});
