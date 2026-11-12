import { useEffect, useState, type FormEvent } from "react";
import { ApiError, getKey, isDemo, paths, request, useApi, type BotHealth as Health } from "../api";
import { ago, exact, num, when } from "../format";
import { href } from "../router";
import { Button, Card, ErrorNotice, Load, PageTitle, StatTile, StateBadge } from "../components/ui";

const STATE_TEXT = {
  learning: "Collecting answers to learn what normal looks like. Nothing is flagged yet (needs 30 answers).",
  provisional: "Answers are flagged on this dashboard, but no Slack alerts or investigations yet, until the threshold is stable and traffic spans 2 hours.",
  ready: "Detection is on: incidents open, investigations run and Slack messages are sent.",
};

function SlackCard({ slack, onChange }: { slack: NonNullable<Health["slack"]>; onChange: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const save = async (webhook: string | null) => {
    setBusy(true); setErr(null);
    try {
      await request("/bot/slack", { method: "POST", body: { webhook }, key: getKey() });
      setUrl(""); onChange();
    } catch (e) { setErr(e instanceof ApiError ? e : new ApiError(0, String(e))); }
    finally { setBusy(false); }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void save(url.trim()); };
  return (
    <Card title="Slack for this bot">
      {slack.connected ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-2 text-sm"><i className="size-1.5 rounded-full bg-ok" aria-hidden />
            Connected <span className="num text-muted">(webhook …{slack.ends_with})</span></span>
          <Button variant="ghost" className="ml-auto" disabled={busy} onClick={() => void save(null)}>Disconnect</Button>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 sm:items-end">
          <label className="flex-1 min-w-0">
            <span className="block text-sm text-muted mb-1.5">Incoming-webhook URL (Slack: Apps → Incoming Webhooks). Incidents and the daily digest go there; without it, they stay on this dashboard.</span>
            <input value={url} onChange={e => setUrl(e.target.value)} type="url" autoComplete="off" spellCheck={false}
                   placeholder="https://hooks.slack.com/services/…" className="w-full h-11 px-4 rounded-[10px] bg-raised border border-control text-text font-mono text-sm placeholder:text-subtle" />
          </label>
          <Button variant="primary" type="submit" disabled={busy || !url.trim()}>{busy ? "Testing…" : "Connect"}</Button>
        </form>
      )}
      <p className="text-xs text-subtle mt-3 mb-0">We send a test message first. The URL is never shown again or logged.</p>
      {err && <div className="mt-3"><ErrorNotice error={err} /></div>}
    </Card>
  );
}

function ReviewCard({ r }: { r: NonNullable<Health["baseline_review"]> }) {
  return (
    <Card title="Baseline review">
      <p className="m-0 text-sm text-muted">
        When it became ready it learned what normal looks like from <span className="num text-text">{r.answers}</span> answers
        ({r.spot_checks.checked} spot-checked). {r.verdict === "looks fine" ? "Nothing unusual in them." : "Before that becomes the reference, check these:"}
      </p>
      {r.issues.length > 0 && (
        <ul className="mt-3 mb-0 pl-5 space-y-2 list-disc marker:text-subtle">
          {r.issues.map(i => (
            <li key={i.category}>{i.text}{" "}
              <span className="inline-flex flex-wrap gap-1 align-middle">{i.examples.map(x => <span key={x} className="num text-xs rounded-sm px-1.5 py-px border border-line-strong bg-raised">{x}</span>)}</span>
            </li>
          ))}
        </ul>
      )}
      <PartlyNote n={r.spot_checks.partly_supported} />
      <p className="text-xs text-subtle mt-3 mb-0">{when(r.created_at)}</p>
    </Card>
  );
}

/** Spot-checks judged "partly supported" by the context: a neutral note, never a problem. */
function PartlyNote({ n }: { n?: number }) {
  if (!n) return null;
  return (
    <p className="text-sm text-muted mt-3 mb-0">
      {n} spot-checked answer{n > 1 ? "s were" : " was"} partly supported by the context: {n > 1 ? "they add" : "it adds"} details
      your documents don't state. Not counted as a problem.
    </p>
  );
}

interface Digest { day: string; lines: string[]; summary: string; slack_sent: boolean; spot_partly?: number }

function DigestCard() {
  const q = useApi<{ digests: Digest[] }>(isDemo() ? "/demo/volt/digests" : "/bot/digests");
  const d = q.data?.digests?.[0];
  if (!d) return null;
  return (
    <Card title="Daily digest">
      <ul className="m-0 p-0 list-none space-y-1.5 text-sm">
        {d.lines.map((l, i) => <li key={i} className={i === 0 ? "font-medium text-text" : "text-muted"}>{l.replace(/\*/g, "")}</li>)}
      </ul>
      <PartlyNote n={d.spot_partly} />
      <p className="text-xs text-subtle mt-3 mb-0">Sent at 20:30 IST every day{d.slack_sent ? " · also posted to Slack" : ""}.</p>
    </Card>
  );
}

export default function BotHealth() {
  const q = useApi<Health>(paths.health());
  useEffect(() => { const t = setInterval(() => q.reload(true), 60_000); return () => clearInterval(t); }, [q.reload]);

  return (
    <>
      <PageTitle sub={q.data?.company}>Bot health</PageTitle>
      <Load q={q} rows={2}>
        {h => (
          <div className="space-y-4">
            <Card title="State">
              <div className="flex flex-wrap items-center gap-3">
                <StateBadge state={h.learning_state} />
                {h.ready_at && <span className="text-sm text-muted" title={exact(h.ready_at)}>ready since {when(h.ready_at)}</span>}
                {h.open_incidents > 0 && (
                  <a href={href({ name: "incidents" })} className="text-sm text-bad">{h.open_incidents} open incident{h.open_incidents > 1 ? "s" : ""}</a>
                )}
              </div>
              <p className="text-muted mt-3 mb-0 max-w-[70ch]">{STATE_TEXT[h.learning_state]}</p>
            </Card>
            {h.baseline_review && <ReviewCard r={h.baseline_review} />}
            <DigestCard />
            {!isDemo() && h.slack && <SlackCard slack={h.slack} onChange={() => q.reload(true)} />}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <StatTile label="Threshold (distance from normal)"
                value={h.threshold === null ? "-" : <>{num(h.threshold)} <span className="text-md text-muted">± {num(h.threshold_margin)}</span></>}
                context={h.threshold === null ? "set after 30 answers" : `± ${h.threshold_margin_pct}% · 90% interval`} />
              <StatTile label="Answers seen" value={h.answers_total.toLocaleString("en-IN")}
                context={`${h.answers_24h.toLocaleString("en-IN")} in the last 24 h`} />
              <StatTile label="Flagged, last 24 h"
                value={h.answers_24h ? `${h.flagged_24h} of ${h.answers_24h}` : "-"}
                tone={h.flagged_24h > 0 ? "bad" : undefined}
                context={`${h.nonanswers_24h} refusals or empty answers`} />
              <StatTile label="Last answer received" value={ago(h.last_answer_at)}
                context={<span title={exact(h.last_answer_at)}>{when(h.last_answer_at)}</span>} />
            </div>
          </div>
        )}
      </Load>
    </>
  );
}
