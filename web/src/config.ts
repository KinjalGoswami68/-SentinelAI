// Everything the M12 rename touches lives here.
export const PRODUCT_NAME = "SentinelAI";
export const TAGLINE = "Your chatbot changed and nobody told you.";
// VITE_API_BASE overrides it for local testing against a local API (never set in the Pages build).
export const API_BASE: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE ?? "https://sentinelai-qqph.onrender.com";
export const REPO_URL = "https://github.com/KinjalGoswami68/-SentinelAI";
export const SCORECARD_URL = `${REPO_URL}/blob/main/docs/SCORECARD.md`;

// Demo bot (key-free read-only endpoints, docs/API.md).
export const DEMO_NAME = "Volt Electronics";
export const DEMO_BLURB = "a fictional support bot fed by real fixture traffic";

// A request slower than this shows "the server is waking up" (Render free tier sleeps).
export const COLD_START_MS = 5000;
