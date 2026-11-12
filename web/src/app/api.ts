import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, COLD_START_MS } from "../config";

// ---- response shapes (docs/API.md) -------------------------------------------------

export type LearningState = "learning" | "provisional" | "ready";

export interface BotHealth {
  company: string;
  learning_state: LearningState;
  ready_at: string | null;
  threshold: number | null;
  threshold_margin: number | null;
  threshold_margin_pct: number | null;
  answers_total: number;
  answers_24h: number;
  flagged_24h: number;
  nonanswers_24h: number;
  flagged_rate_24h: number | null;
  last_answer_at: string | null;
  open_incidents: number;
  slack?: { connected: boolean; ends_with: string | null };
}

export interface IncidentSummary {
  id: number;
  status: "open" | "resolved";
  severity: 1 | 2 | 3;
  severity_label: string;
  opened_at: string;
  closed_at: string | null;
  flapping: boolean;
  reopen_count: number;
  reasons: string[];
  cause: string | null;
  confidence: number | null;
}

export interface Example {
  request_id: string | null;
  prompt: string;
  response: string;
  flagged?: boolean | number;
  prompt_version?: string | null;
  model_version?: string | null;
}

export interface Claim { text: string; evidence_ids: string[] }

export interface Note {
  investigation_id: number;
  cause: string;
  confidence: number;
  written_by: string;
  fallback_reason: string | null;
  report: {
    cause: string;
    confidence: number;
    headline: string;
    claims: Claim[];
    example_ids: string[];
    suggested_check: string;
  };
  slack_text?: string | null;
}

export interface SeriesPoint {
  t: string;
  request_id: string | null;
  distance: number | null;
  flagged: boolean | number;
  nonanswer: boolean | number;
  prompt_version: string | null;
  model_version: string | null;
}

export interface VersionSpan { field: string; value: string; first_seen: string; last_seen: string; answers: number }

export interface IncidentDetail {
  company: string;
  incident: IncidentSummary & { last_signal_at: string | null; signal_count: number; slack_messages: number };
  note: Note | null;
  evidence: { flagged_examples: Example[]; baseline_examples: Example[]; items: unknown[] };
  distance_series: { threshold: number | null; points: SeriesPoint[] };
  version_timeline: VersionSpan[];
}

export interface Registered { company_id: number; company_name: string; api_key: string }

export interface AskClaim { text: string; ids: string[] }
export interface AskResult {
  claims: AskClaim[]; dropped: number; tools_used: string[]; tokens: number;
  stopped: string | null; model: string; questions_left_today: number;
}

export interface TryAnswer { response: string; prompt?: string; request_id?: string; t?: string; prompt_version?: string; model_version?: string }
export interface TryResult {
  learning: { state: LearningState; why: string | null; answers: number; threshold: number | null; threshold_margin: number | null };
  after: { answers: number; flagged: number; nonanswers: number };
  incident: null | { opened_after_answers: number; severity: number; reasons: string[]; trigger: string };
  note: null | { cause: string; confidence: number; written_by: string; fallback_reason: string | null;
                 quota_used_up: boolean; report: Note["report"] };
  distance_series: { threshold: number | null; change_at: string | null; points: SeriesPoint[] };
  timings_s: { embed: number; detect: number; note: number; total: number; texts_embedded: number };
}

/** Ask the agent about an incident (demo: key-free route). */
export const askPath = (id: number | string) => (isDemo() ? `/demo/volt/incidents/${id}/ask` : `/incidents/${id}/ask`);

// ---- mode, key, paths -------------------------------------------------------------

export const isDemo = () => /\/app\/demo(\/|$)/.test(window.location.pathname);

const KEY = "shiftlens.api_key";   // stored with the theme preferences, nothing else
const OLD_KEY = "sentinelai.api_key";   // before the rename (2026-11-11): moved once, then removed
export const getKey = (): string | null => {
  try {
    const old = localStorage.getItem(OLD_KEY);
    if (old !== null) {
      if (localStorage.getItem(KEY) === null) localStorage.setItem(KEY, old);
      localStorage.removeItem(OLD_KEY);
    }
    return localStorage.getItem(KEY);
  } catch { return null; }
};
export const setKey = (k: string) => { try { localStorage.setItem(KEY, k.trim()); } catch { /* private mode */ } };
export const clearKey = () => { try { localStorage.removeItem(KEY); } catch { /* ignore */ } };

export const paths = {
  health: () => (isDemo() ? "/demo/volt/health" : "/bot/health"),
  incidents: () => (isDemo() ? "/demo/volt/incidents" : "/incidents"),
  incident: (id: number | string) => (isDemo() ? `/demo/volt/incidents/${id}` : `/incidents/${id}`),
};

// ---- request ----------------------------------------------------------------------

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) { super(message); }
}

interface Opts { method?: "GET" | "POST"; body?: unknown; key?: string | null; onSlow?: () => void; signal?: AbortSignal; timeoutMs?: number }

export async function request<T>(path: string, o: Opts = {}): Promise<T> {
  const slow = o.onSlow ? window.setTimeout(o.onSlow, COLD_START_MS) : undefined;
  const ctl = new AbortController();
  const timeout = window.setTimeout(() => ctl.abort(), o.timeoutMs ?? 100_000);   // a cold start takes ~60 s
  o.signal?.addEventListener("abort", () => ctl.abort());
  const headers: Record<string, string> = {};
  if (o.key) headers["X-API-Key"] = o.key;
  if (o.body !== undefined) headers["Content-Type"] = "application/json";
  try {
    const r = await fetch(API_BASE + path, {
      method: o.method ?? "GET", headers, signal: ctl.signal,
      body: o.body === undefined ? undefined : JSON.stringify(o.body),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const j = await r.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* not json */ }
      const ra = Number(r.headers.get("Retry-After")) || undefined;
      throw new ApiError(r.status, detail, ra);
    }
    return (await r.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (ctl.signal.aborted && !o.signal?.aborted) throw new ApiError(0, "The server did not answer in time.");
    if (o.signal?.aborted) throw e;
    throw new ApiError(0, "Could not reach the server. Check your connection.");
  } finally {
    window.clearTimeout(slow);
    window.clearTimeout(timeout);
  }
}

// ---- hook -------------------------------------------------------------------------

export interface Loadable<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  slow: boolean;          // over COLD_START_MS: show "the server is waking up"
  reload: (quiet?: boolean) => void;
}

/** GET `path` (null = skip). Keeps the last data while reloading quietly (polling). */
export function useApi<T>(path: string | null): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [slow, setSlow] = useState(false);
  const [tick, setTick] = useState(0);
  const quietRef = useRef(false);

  useEffect(() => {
    if (path === null) return;
    const ctl = new AbortController();
    if (!quietRef.current) { setLoading(true); setError(null); }
    setSlow(false);
    request<T>(path, { key: isDemo() ? null : getKey(), signal: ctl.signal, onSlow: () => setSlow(true) })
      .then(d => { setData(d); setError(null); })
      .catch(e => { if (!ctl.signal.aborted) setError(e instanceof ApiError ? e : new ApiError(0, String(e))); })
      .finally(() => { if (!ctl.signal.aborted) { setLoading(false); setSlow(false); quietRef.current = false; } });
    return () => ctl.abort();
  }, [path, tick]);

  const reload = useCallback((quiet = false) => { quietRef.current = quiet; setTick(t => t + 1); }, []);
  return { data, error, loading, slow, reload };
}
