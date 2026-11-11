import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";
import * as config from "./src/config";
import { THEME_BOOT } from "./src/theme";

// GitHub Pages serves this repo at https://kinjalgoswami68.github.io/<repo name>/ (ShiftLens).
// pages.yml sets PAGES_BASE from the repository name, so the build follows a repo rename.
// Pages: landing at /, the app at /app/ and the public demo at /app/demo/.
export default defineConfig({
  base: process.env.PAGES_BASE ?? "/ShiftLens/",
  plugins: [react(), tailwindcss(), {
    // %PRODUCT_NAME%, %TAGLINE%, %API_BASE%, %REPO_URL%, %SCORECARD_URL% in the HTML come from src/config.ts
    name: "config-tokens",
    transformIndexHtml: html => html
      .replace("%THEME_BOOT%", () => THEME_BOOT)
      .replace(/%(PRODUCT_NAME|TAGLINE|API_BASE|REPO_URL|SCORECARD_URL)%/g, (_m, k: keyof typeof config) => String(config[k])),
  }],
  build: {
    target: "es2022",
    rollupOptions: {
      input: {
        landing: resolve(import.meta.dirname, "index.html"),
        app: resolve(import.meta.dirname, "app/index.html"),
        demo: resolve(import.meta.dirname, "app/demo/index.html"),
      },
    },
  },
});
