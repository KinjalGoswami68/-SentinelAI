import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, request, type Note, type TryResult } from "../api";
import { parseAnswers, splitByTime, TEMPLATE_CSV, type Answer } from "../csv";
import { num } from "../format";
import { PRODUCT_NAME } from "../../config";
import { NoteCard } from "./IncidentDetail";
import { AsciiSpinner, Button, Card, ErrorNotice, PageTitle, SevBadge, Skeleton, StateBadge, StatTile, WakingUp, WarnNote } from "../components/ui";

const DistanceChart = lazy(() => import("../components/DistanceChart"));

// Same limits as the server (tryit.py): sized so a try finishes in about a minute on the free server.
const LIMITS = { before: [30, 75], after: [10, 30] } as const;
const SECONDS_PER_ANSWER = 0.46;      // measured on Render, 2026-11-12

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([TEMPLATE_CSV], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "shiftlens-template.csv" });
  a.click(); URL.revokeObjectURL(url);
}

function Elapsed({ expected }: { expected: number }) {
  const [t0] = useState(() => Date.now());
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick(x => x + 1), 1000); return () => clearInterval(i); }, []);
  const s = Math.floor((Date.now() - t0) / 1000);
  return <span className="num">{s} s of about {expected} s</span>;
}

function Result({ r }: { r: TryResult }) {
  const note: Note | null = r.note ? { investigation_id: 0, cause: r.note.cause, confidence: r.note.confidence,
    written_by: r.note.written_by, fallback_reason: r.note.fallback_reason, report: r.note.report } : null;
  return (
    <div className="space-y-4 rise">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatTile label="Normal learned from" value={`${r.learning.answers} answers`}
          context={<span className="inline-flex items-center gap-2"><StateBadge state={r.learning.state} />
            {r.learning.threshold !== null && <>threshold <span className="num">{num(r.learning.threshold)} ± {num(r.learning.threshold_margin)}</span></>}</span>} />
        <StatTile label="Answers checked" value={`${r.after.flagged} of ${r.after.answers}`} tone={r.after.flagged ? "bad" : undefined}
          context={`flagged as unusual · ${r.after.nonanswers} refusals or empty`} />
        <StatTile label="Incident" value={r.incident ? `after ${r.incident.opened_after_answers}` : "none"}
          tone={r.incident ? "bad" : "ok"} context={r.incident ? r.incident.reasons.join("; ") : "nothing would have opened"} />
      </div>
      {r.learning.state !== "ready" && (
        <WarnNote>The "before" answers are too few or too varied for a stable threshold ({r.learning.why}). Treat the flags as rough.</WarnNote>
      )}
      {r.incident && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <SevBadge severity={r.incident.severity} /> opened by {r.incident.trigger === "version regression" ? "the version check" : "the answers' signals"}
        </div>
      )}
      {r.note?.quota_used_up && <WarnNote>Today's free model quota is used up, so this note was written by the rules alone.</WarnNote>}
      {note ? <NoteCard note={note} /> : (
        <Card title="Investigation note"><p className="m-0 text-muted">No incident, so no investigation: the "after" answers look like the "before" ones.</p></Card>
      )}
      <Card title="Distance from normal (the after answers)">
        <Suspense fallback={<Skeleton rows={1} />}>
          <DistanceChart points={r.distance_series.points} threshold={r.distance_series.threshold}
                         windowStart={r.distance_series.change_at} windowEnd={null} />
        </Suspense>
      </Card>
      <p className="text-xs text-subtle m-0">Took {r.timings_s.total} s ({r.timings_s.texts_embedded} answers embedded). Nothing was stored.</p>
    </div>
  );
}

export default function TryPage() {
  const [text, setText] = useState("");
  const [splitAt, setSplitAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [result, setResult] = useState<TryResult | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const parsed = useMemo(() => (text.trim() ? parseAnswers(text) : null), [text]);
  const groups = useMemo(() => {
    if (!parsed) return null;
    if (parsed.before || parsed.after) return { before: parsed.before ?? [], after: parsed.after ?? [], errors: parsed.errors };
    if (parsed.all) {
      if (!splitAt) return { before: [] as Answer[], after: [] as Answer[], errors: parsed.errors, needsSplit: true };
      const s = splitByTime(parsed.all, splitAt);
      return { before: s.before, after: s.after, errors: [...parsed.errors, ...s.errors], needsSplit: true };
    }
    return { before: [] as Answer[], after: [] as Answer[], errors: parsed.errors };
  }, [parsed, splitAt]);
  const sizeProblems = groups ? ([["before", groups.before.length], ["after", groups.after.length]] as const)
    .filter(([g, n]) => n < LIMITS[g][0] || n > LIMITS[g][1])
    .map(([g, n]) => `"${g}" has ${n} answers; it needs ${LIMITS[g][0]}-${LIMITS[g][1]}`) : [];
  const ready = !!groups && (!("needsSplit" in groups) || !!splitAt) && groups.errors.length === 0 && sizeProblems.length === 0;
  const expected = groups ? Math.round((groups.before.length + groups.after.length) * SECONDS_PER_ANSWER + 6) : 0;

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 400_000) { setErr(new ApiError(413, "That file is over 400 KB.")); return; }
    setErr(null); setResult(null); setText(await f.text());
  };
  const run = async () => {
    if (!groups || !ready) return;
    setBusy(true); setSlow(false); setErr(null); setResult(null);
    try {
      setResult(await request<TryResult>("/try", { method: "POST", body: { before: groups.before, after: groups.after },
        onSlow: () => setSlow(true), timeoutMs: 200_000 }));
    } catch (e) { setErr(e instanceof ApiError ? e : new ApiError(0, String(e))); }
    finally { setBusy(false); setSlow(false); }
  };

  return (
    <>
      <PageTitle sub="Paste or upload your bot's answers. Nothing is stored.">Try without code</PageTitle>
      <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] gap-4 items-start">
        <Card title="1. Your answers">
          <p className="text-sm text-muted mt-0 mb-4">
            CSV or JSON. Either a <span className="num text-text">group</span> column with <span className="num text-text">before</span> /
            <span className="num text-text"> after</span>, or one list with times in <span className="num text-text">t</span> that you split below.
            Needs a <span className="num text-text">response</span> column; <span className="num text-text">prompt</span>,
            <span className="num text-text"> prompt_version</span> and <span className="num text-text">model_version</span> help.
          </p>
          <textarea value={text} onChange={e => { setText(e.target.value); setResult(null); }} rows={9} spellCheck={false}
                    aria-label="Answers as CSV or JSON" placeholder={TEMPLATE_CSV}
                    className="w-full px-4 py-3 rounded-[10px] bg-raised border border-control text-text font-mono text-xs leading-5 placeholder:text-subtle" />
          <div className="flex flex-wrap gap-3 mt-3">
            <input ref={file} type="file" accept=".csv,.json,text/csv,application/json" className="sr-only" onChange={e => void onFile(e.target.files?.[0])} />
            <Button onClick={() => file.current?.click()}>Upload CSV or JSON</Button>
            <Button variant="ghost" onClick={downloadTemplate}>Download template</Button>
          </div>
        </Card>
        <Card title="2. Check and run">
          {!groups ? <p className="m-0 text-muted">Paste or upload answers to see the counts here.</p> : (
            <div className="space-y-4">
              {"needsSplit" in groups && (
                <label className="block">
                  <span className="block text-sm text-muted mb-1.5">One list: when did the change happen? Answers before this time are "before".</span>
                  <input type="datetime-local" value={splitAt} onChange={e => setSplitAt(e.target.value)}
                         className="h-11 px-3 rounded-[10px] bg-raised border border-control text-text" />
                </label>
              )}
              <dl className="grid grid-cols-2 gap-3 m-0">
                {(["before", "after"] as const).map(g => (
                  <div key={g} className="rounded-[10px] border border-line p-3">
                    <dt className="label">{g}</dt>
                    <dd className="m-0 mt-2 num text-lg">{groups[g].length} <span className="text-xs text-subtle">of {LIMITS[g][0]}-{LIMITS[g][1]}</span></dd>
                  </div>
                ))}
              </dl>
              {[...groups.errors.slice(0, 4), ...sizeProblems].map((m, i) => <WarnNote key={i}>{m}</WarnNote>)}
              <Button variant="primary" onClick={() => void run()} disabled={!ready || busy}>{busy ? "Running…" : `Run ${PRODUCT_NAME} on these`}</Button>
              <p className="text-xs text-subtle m-0">About {expected || "60"} s on the free server. 3 tries per hour, 15 a day for everyone.</p>
            </div>
          )}
        </Card>
      </div>
      <div className="mt-6 space-y-4" aria-live="polite">
        {busy && (
          <div className="panel p-5 space-y-3">
            <p className="m-0 font-mono text-sm"><AsciiSpinner /> embedding and checking your answers · <Elapsed expected={expected} /></p>
            {slow && <WakingUp />}
          </div>
        )}
        {err && <ErrorNotice error={err} onRetry={ready ? () => void run() : undefined} />}
        {result && <Result r={result} />}
      </div>
    </>
  );
}
