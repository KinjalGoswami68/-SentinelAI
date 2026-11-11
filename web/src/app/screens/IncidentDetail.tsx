import { lazy, Suspense } from "react";
import { paths, useApi, type Example, type IncidentDetail as Detail, type Note } from "../api";
import { duration, exact, num, when, words } from "../format";
import { href } from "../router";
import { Card, Load, SevBadge, Skeleton, StatusBadge } from "../components/ui";

const DistanceChart = lazy(() => import("../components/DistanceChart"));

function Rid({ id, hot }: { id: string; hot?: boolean }) {
  return <span className={`num text-xs rounded-sm px-1.5 py-px border ${hot ? "border-bad/50 text-bad bg-bad-tint" : "border-line-strong bg-raised text-text"}`}>{id}</span>;
}

function NoteCard({ note }: { note: Note | null }) {
  if (!note) {
    return <Card title="Investigation note"><p className="text-muted m-0">No investigation ran for this incident (one per bot per 30 min, 20 per day in total).</p></Card>;
  }
  const r = note.report;
  const byRules = note.written_by === "rules";
  return (
    <Card title="Investigation note">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <b className="font-mono font-medium text-lg">{r.cause}</b>
        <span className="num text-muted">confidence {num(r.confidence, 2)}</span>
      </div>
      {r.headline && <p className="mt-2 mb-0 max-w-[70ch]">{r.headline}</p>}
      {r.claims.length > 0 && (
        <ul className="mt-3 mb-0 pl-5 space-y-2 list-disc marker:text-subtle">
          {r.claims.map((c, i) => (
            <li key={i}>
              {c.text}{" "}
              <span className="inline-flex flex-wrap gap-1 align-middle">{c.evidence_ids.map(id => <Rid key={id} id={id} />)}</span>
            </li>
          ))}
        </ul>
      )}
      {r.suggested_check && <p className="mt-3 mb-0 text-muted"><span className="text-text">Check:</span> {r.suggested_check}</p>}
      {r.example_ids.length > 0 && (
        <p className="mt-3 mb-0 text-sm text-muted">Example requests: <span className="inline-flex flex-wrap gap-1 align-middle">{r.example_ids.map(id => <Rid key={id} id={id} hot />)}</span></p>
      )}
      <p className="text-xs text-subtle mt-4 mb-0">
        {byRules
          ? <>Written by the rules-only fallback{note.fallback_reason ? ` (${note.fallback_reason})` : ""}.</>
          : <>Written by an LLM ({note.written_by.replace(/^openai\//, "")}) from the evidence above; its confidence is capped by the rules.</>}
      </p>
    </Card>
  );
}

function ExampleCard({ e, flagged }: { e: Example; flagged: boolean }) {
  const ver = [e.prompt_version && `prompt ${e.prompt_version}`, e.model_version && `model ${e.model_version}`].filter(Boolean).join(" · ");
  return (
    <article className={`rounded-md border p-3 ${flagged ? "border-bad/40" : "border-line"} bg-bg`}>
      <div className="flex items-center gap-2 text-xs text-subtle mb-2">
        {e.request_id && <Rid id={e.request_id} hot={flagged} />}
        {ver && <span className="num">{ver}</span>}
      </div>
      <p className="text-sm text-muted m-0"><span className="text-subtle">Q </span>{e.prompt}</p>
      <p className="text-sm m-0 mt-2 whitespace-pre-wrap break-words max-h-48 overflow-auto"><span className="text-subtle">A </span>{e.response}</p>
    </article>
  );
}

function Evidence({ d }: { d: Detail }) {
  const bad = d.evidence.flagged_examples.slice(0, 3), good = d.evidence.baseline_examples.slice(0, 3);
  return (
    <Card title="Evidence: changed answers next to normal ones">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-bad m-0">During the incident</h3>
          {bad.length ? bad.map((e, i) => <ExampleCard key={i} e={e} flagged />) : <p className="text-sm text-muted m-0">No examples.</p>}
        </div>
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-muted m-0">Normal, before it</h3>
          {good.length ? good.map((e, i) => <ExampleCard key={i} e={e} flagged={false} />) : <p className="text-sm text-muted m-0">No examples.</p>}
        </div>
      </div>
    </Card>
  );
}

export default function IncidentDetail({ id }: { id: string }) {
  const q = useApi<Detail>(paths.incident(id));
  return (
    <>
      <a href={href({ name: "incidents" })} className="text-sm text-muted hover:text-text no-underline">← Incidents</a>
      <Load q={q} rows={4}>
        {d => {
          const i = d.incident;
          return (
            <div className="space-y-4 mt-3">
              <header className="mb-6">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="text-xl m-0 mr-2">Incident <span className="num">#{i.id}</span></h1>
                  <SevBadge severity={i.severity} /><StatusBadge status={i.status} />
                </div>
                <p className="text-muted mt-2 mb-0 text-sm">
                  <span title={exact(i.opened_at)}>Opened {when(i.opened_at)}</span>
                  {" · "}{i.status === "open" ? `${duration(i.opened_at, null)} so far` : <span title={exact(i.closed_at)}>lasted {duration(i.opened_at, i.closed_at)}</span>}
                  {" · "}{i.signal_count} signal{i.signal_count === 1 ? "" : "s"} · {i.slack_messages} Slack message{i.slack_messages === 1 ? "" : "s"}
                  {i.flapping && ` · reopened ${i.reopen_count}×`}
                </p>
                {i.reasons.length > 0 && <p className="text-sm text-subtle mt-1 mb-0">Why it opened: {i.reasons.map(words).join("; ")}</p>}
              </header>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
                <NoteCard note={d.note} />
                <Evidence d={d} />
              </div>
              <Card title="Distance from normal">
                <Suspense fallback={<Skeleton rows={1} />}>
                  <DistanceChart points={d.distance_series.points} threshold={d.distance_series.threshold}
                                 windowStart={i.opened_at} windowEnd={i.closed_at} />
                </Suspense>
              </Card>
            </div>
          );
        }}
      </Load>
    </>
  );
}
