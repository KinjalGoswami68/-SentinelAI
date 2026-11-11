import { useEffect, useRef, useState, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { ApiError, LearningState, Loadable } from "../api";
import { mountThemeControls } from "../../theme";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ---- button: chrome (primary), line (secondary), ghost -------------------------------

type Variant = "primary" | "secondary" | "ghost";
const BTN: Record<Variant, string> = { primary: "btn btn-chrome", secondary: "btn btn-line", ghost: "btn btn-ghost" };

export function Button({ variant = "secondary", className, ...p }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type="button" className={cx(BTN[variant], className)} {...p} />;
}

export function LinkButton({ variant = "secondary", className, ...p }:
  AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: Variant }) {
  return <a className={cx(BTN[variant], "no-underline", className)} {...p} />;
}

// ---- badges (always a word, never colour alone) -------------------------------------

const BADGE = "inline-flex items-center gap-1.5 h-[22px] px-2 rounded-[6px] border font-mono text-[11px] font-medium tracking-[.04em] whitespace-nowrap";
const TONE = {
  grey: "text-muted border-line-strong",
  ok: "text-ok border-ok/45 bg-ok-tint",
  warn: "text-warn border-warn/45 bg-warn-tint",
  bad: "text-bad border-bad/50 bg-bad-tint",
  sev1: "text-white border-sev1 bg-sev1",
};

export function StateBadge({ state }: { state: LearningState }) {
  return <span className={cx(BADGE, state === "ready" ? TONE.ok : state === "provisional" ? TONE.warn : TONE.grey)}>{state}</span>;
}

export function SevBadge({ severity }: { severity: number }) {
  return <span className={cx(BADGE, severity === 1 ? TONE.sev1 : severity === 2 ? TONE.bad : TONE.warn)}>SEV{severity}</span>;
}

export function StatusBadge({ status }: { status: "open" | "resolved" }) {
  return status === "open"
    ? <span className={cx(BADGE, TONE.bad)}><i className="size-1.5 rounded-full bg-current" aria-hidden />open</span>
    : <span className={cx(BADGE, TONE.grey)}>resolved</span>;
}

// ---- panel, stat tile, empty state ----------------------------------------------------

export function Card({ title, children, className, action }:
  { title?: ReactNode; children: ReactNode; className?: string; action?: ReactNode }) {
  return (
    <section className={cx("panel p-4 sm:p-6", className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-4 mb-4">
          {title && <h2 className="label m-0 tracking-[.08em]">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatTile({ label, value, context, tone }:
  { label: string; value: ReactNode; context?: ReactNode; tone?: "ok" | "warn" | "bad" }) {
  const t = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div className="panel hot p-5 sm:p-6">
      <div className="label">{label}</div>
      <div className={cx("num font-display text-[28px] leading-none font-semibold tracking-tight mt-4", t)}>{value}</div>
      {context && <div className="text-xs text-subtle mt-3">{context}</div>}
    </div>
  );
}

const QUIET = `   .-----------.
  /  ·  ·  ·  · \\
 |  ·  ·  ·  ·  |
  \\ ·  ·  ·  · /
   '-----------'`;

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="panel p-6 sm:p-8 grid gap-6 sm:grid-cols-[auto_1fr] items-center">
      <pre className="m-0 font-mono text-[11px] leading-[14px] text-subtle select-none" aria-hidden>{QUIET}</pre>
      <div>
        <h2 className="text-lg m-0">{title}</h2>
        {children && <div className="text-muted mt-1.5">{children}</div>}
        {action && <div className="mt-5">{action}</div>}
      </div>
    </div>
  );
}

// ---- terminal code block with copy --------------------------------------------------

export function CodeBlock({ code, label = "code", file }: { code: string; label?: string; file?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* denied */ }
  };
  return (
    <div className="term">
      <div className="bar"><i aria-hidden />{file ?? label}
        <button type="button" className="copy" onClick={copy} aria-label={`Copy ${label}`}>{copied ? "copied" : "copy"}</button>
      </div>
      <pre><code>{code}</code></pre>
      <span className="sr-only" aria-live="polite">{copied ? `${label} copied` : ""}</span>
    </div>
  );
}

// ---- notices: waking up, error, loading ---------------------------------------------

const FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
export function AsciiSpinner() {
  const [k, setK] = useState(0);
  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setK(x => (x + 1) % FRAMES.length), 90);
    return () => clearInterval(t);
  }, []);
  return <span className="spin-ascii" aria-hidden>{FRAMES[k]}</span>;
}

function Elapsed() {
  const [s, setS] = useState(5);
  useEffect(() => { const t = setInterval(() => setS(x => x + 1), 1000); return () => clearInterval(t); }, []);
  return <span className="num text-xs text-subtle ml-auto">{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</span>;
}

export function WakingUp() {
  return (
    <div role="status" className="panel flex items-center gap-3 px-4 py-3 text-sm">
      <AsciiSpinner />
      The server is waking up (free tier), about a minute.
      <Elapsed />
    </div>
  );
}

const WARN_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-warn shrink-0" aria-hidden>
    <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </svg>
);

export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const text = error.status === 401 ? "This API key was not accepted. Sign out and paste it again."
    : error.status === 404 ? "Not found."
    : error.status === 429 ? `${error.message}${error.retryAfter ? ` Try again in ${Math.ceil(error.retryAfter / 60)} min.` : ""}`
    : error.status ? `${error.message} (HTTP ${error.status}).` : error.message;
  return (
    <div role="alert" className="flex items-center gap-3 px-4 py-3 rounded-[10px] border border-warn/45 bg-warn-tint text-sm">
      {WARN_ICON}
      <span>{text}</span>
      {onRetry && error.status !== 401 && error.status !== 404 && (
        <Button variant="ghost" className="ml-auto !h-8 text-sm" onClick={onRetry}>Retry</Button>
      )}
    </div>
  );
}

export function WarnNote({ children }: { children: ReactNode }) {
  return <div role="alert" className="flex gap-3 items-start px-4 py-3 rounded-[10px] border border-warn/45 bg-warn-tint text-sm">{WARN_ICON}<span>{children}</span></div>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-hidden>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="h-14 panel animate-pulse" />)}
    </div>
  );
}

/** Loading (skeleton, then "waking up" after 5 s), error, or the content. */
export function Load<T>({ q, children, rows }: { q: Loadable<T>; children: (d: T) => ReactNode; rows?: number }) {
  if (q.data !== null && !q.error) return <>{children(q.data)}</>;
  if (q.error) return <ErrorNotice error={q.error} onRetry={() => q.reload()} />;
  return (
    <div className="space-y-4">
      {q.slow && <WakingUp />}
      <span className="sr-only" role="status">Loading</span>
      <Skeleton rows={rows} />
    </div>
  );
}

export function PageTitle({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-8">
      <h1 className="m-0 text-[clamp(30px,3.4vw,40px)] leading-[1.08] tracking-[-.035em]">{children}</h1>
      {sub && <p className="text-muted mt-2 m-0 font-mono text-xs uppercase tracking-[.1em]">{sub}</p>}
    </div>
  );
}

/** Theme toggle + intensity fader (shared with the landing page; compact in the app header). */
export function ThemeControls() {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => (host.current ? mountThemeControls(host.current) : undefined), []);
  return <div ref={host} />;
}
