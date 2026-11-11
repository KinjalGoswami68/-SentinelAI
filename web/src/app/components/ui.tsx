import { useEffect, useState, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { ApiError, LearningState, Loadable } from "../api";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ---- button -----------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost";
const BTN: Record<Variant, string> = {
  primary: "bg-ok text-on-ok hover:bg-ok-hover",
  secondary: "border border-control text-text hover:bg-raised",
  ghost: "text-muted hover:text-text hover:bg-raised px-2.5",
};
const BTN_BASE =
  "inline-flex items-center justify-center gap-2 h-10 px-4 rounded-md text-base font-medium cursor-pointer " +
  "transition-colors duration-150 ease-out disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap";

export function Button({ variant = "secondary", className, ...p }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type="button" className={cx(BTN_BASE, BTN[variant], className)} {...p} />;
}

export function LinkButton({ variant = "secondary", className, ...p }:
  AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: Variant }) {
  return <a className={cx(BTN_BASE, BTN[variant], className)} {...p} />;
}

// ---- badges (always a word, never colour alone) -------------------------------------

const BADGE = "inline-flex items-center gap-1.5 h-5 px-2 rounded-sm text-xs font-medium whitespace-nowrap";

export function StateBadge({ state }: { state: LearningState }) {
  const c = state === "ready" ? "bg-ok-tint text-ok" : state === "provisional" ? "bg-warn-tint text-warn" : "bg-raised text-muted";
  return <span className={cx(BADGE, c)}>{state}</span>;
}

export function SevBadge({ severity }: { severity: number }) {
  const c = severity === 1 ? "bg-bad text-bg" : severity === 2 ? "bg-bad-tint text-bad" : "bg-warn-tint text-warn";
  return <span className={cx(BADGE, "font-mono", c)}>SEV{severity}</span>;
}

export function StatusBadge({ status }: { status: "open" | "resolved" }) {
  return status === "open"
    ? <span className={cx(BADGE, "bg-bad-tint text-bad")}><i className="size-1.5 rounded-full bg-current" aria-hidden />open</span>
    : <span className={cx(BADGE, "bg-raised text-muted")}>resolved</span>;
}

// ---- card, stat tile, empty state ---------------------------------------------------

export function Card({ title, children, className, action }:
  { title?: ReactNode; children: ReactNode; className?: string; action?: ReactNode }) {
  return (
    <section className={cx("bg-surface border border-line rounded-lg p-4 sm:p-5", className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-4 mb-3">
          {title && <h2 className="text-sm font-medium text-muted tracking-normal">{title}</h2>}
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
    <div className="bg-surface border border-line rounded-lg p-4 sm:p-5">
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("num text-xl font-medium mt-1", t)}>{value}</div>
      {context && <div className="text-xs text-subtle mt-1">{context}</div>}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="bg-surface border border-line rounded-lg p-6 sm:p-8">
      <h2 className="text-md font-semibold">{title}</h2>
      {children && <div className="text-muted mt-1">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ---- code block with copy -----------------------------------------------------------

export function CodeBlock({ code, label = "code" }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* denied */ }
  };
  return (
    <div className="relative bg-surface border border-line rounded-md">
      <Button variant="ghost" onClick={copy} className="absolute top-2 right-2 h-7 text-xs" aria-label={`Copy ${label}`}>
        {copied ? "copied" : "copy"}
      </Button>
      <pre className="m-0 px-5 pt-10 pb-4 sm:pt-4 sm:pr-20 overflow-x-auto font-mono text-sm text-text"><code>{code}</code></pre>
      <span className="sr-only" aria-live="polite">{copied ? `${label} copied` : ""}</span>
    </div>
  );
}

// ---- notices: waking up, error, loading ---------------------------------------------

function Elapsed() {
  const [s, setS] = useState(5);
  useEffect(() => { const t = setInterval(() => setS(x => x + 1), 1000); return () => clearInterval(t); }, []);
  return <span className="num text-xs text-subtle ml-auto">{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</span>;
}

export function WakingUp() {
  return (
    <div role="status" className="flex items-center gap-3 px-4 py-3 rounded-md border border-line-strong bg-raised text-sm">
      <span className="size-3 rounded-full border-2 border-line-strong border-t-muted animate-spin shrink-0" aria-hidden />
      The server is waking up (free tier), about a minute.
      <Elapsed />
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const text = error.status === 401 ? "This API key was not accepted. Sign out and paste it again."
    : error.status === 404 ? "Not found."
    : error.status === 429 ? `${error.message}${error.retryAfter ? ` Try again in ${Math.ceil(error.retryAfter / 60)} min.` : ""}`
    : error.status ? `${error.message} (HTTP ${error.status}).` : error.message;
  return (
    <div role="alert" className="flex items-center gap-3 px-4 py-3 rounded-md border border-[#5C4A23] bg-raised text-sm">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-warn shrink-0" aria-hidden>
        <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      </svg>
      <span>{text}</span>
      {onRetry && error.status !== 401 && error.status !== 404 && (
        <Button variant="ghost" className="ml-auto h-7 text-sm" onClick={onRetry}>Retry</Button>
      )}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-hidden>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="h-14 rounded-lg bg-surface border border-line animate-pulse" />)}
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
    <div className="mb-6">
      <h1 className="text-xl">{children}</h1>
      {sub && <p className="text-muted mt-1 m-0">{sub}</p>}
    </div>
  );
}
