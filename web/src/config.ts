// Everything the M12 rename touches lives here.
export const PRODUCT_NAME = "ShiftLens";
export const TAGLINE = "Your chatbot changed and nobody told you.";
// VITE_API_BASE overrides it for local testing against a local API (never set in the Pages build).
export const API_BASE: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE ?? "https://sentinelai-qqph.onrender.com";
// The Pages build sets VITE_REPO_URL from the repository it builds (pages.yml), so links stay
// right across a repo rename; the default is the repo's name since M12.
export const REPO_URL: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_REPO_URL ??
  (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env?.VITE_REPO_URL ??
  "https://github.com/KinjalGoswami68/ShiftLens";
export const SCORECARD_URL = `${REPO_URL}/blob/main/docs/SCORECARD.md`;

// Demo bot (key-free read-only endpoints, docs/API.md).
export const DEMO_NAME = "Volt Electronics";
export const DEMO_BLURB = "a fictional support bot fed by real fixture traffic";

// A request slower than this shows "the server is waking up" (Render free tier sleeps).
export const COLD_START_MS = 5000;
