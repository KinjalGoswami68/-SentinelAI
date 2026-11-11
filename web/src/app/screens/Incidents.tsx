import { useEffect } from "react";
import { paths, useApi, type BotHealth, type IncidentSummary } from "../api";
import { ago, duration, exact, num, when } from "../format";
import { href } from "../router";
import { EmptyState, Load, LinkButton, PageTitle, SevBadge, StateBadge, StatusBadge } from "../components/ui";

export function causeText(i: Pick<IncidentSummary, "cause" | "confidence" | "status">) {
  if (i.cause) return <><b className="font-mono font-medium">{i.cause}</b><span className="num text-muted ml-2">{num(i.confidence, 2)}</span></>;
  return <span className="text-muted">{i.status === "open" ? "investigating…" : "no investigation note"}</span>;
}

function Row({ i }: { i: IncidentSummary }) {
  return (
    <a href={href({ name: "incident", id: String(i.id) })}
       className="grid grid-cols-[auto_auto_1fr] md:grid-cols-[72px_104px_150px_110px_minmax(0,1fr)] gap-x-4 gap-y-1.5 items-center
                  px-4 md:px-5 py-3.5 md:py-0 md:h-14 border-b border-line last:border-b-0 text-sm text-text no-underline
                  hover:bg-raised transition-colors duration-150 focus-visible:outline-offset-[-2px]">
      <span><SevBadge severity={i.severity} /></span>
      <span><StatusBadge status={i.status} /></span>
      <span className="num text-muted justify-self-end md:justify-self-start" title={exact(i.opened_at)}>{when(i.opened_at)}</span>
      <span className="num text-muted hidden md:inline">{duration(i.opened_at, i.closed_at)}{i.status === "open" ? " so far" : ""}</span>
      <span className="col-span-3 md:col-span-1 min-w-0 truncate">
        {causeText(i)}
        {i.flapping && <span className="text-xs text-subtle ml-3">reopened {i.reopen_count}×</span>}
      </span>
    </a>
  );
}

export default function Incidents() {
  const list = useApi<{ company: string; incidents: IncidentSummary[] }>(paths.incidents());
  const health = useApi<BotHealth>(paths.health());
  useEffect(() => {                                   // quiet refresh every minute
    const t = setInterval(() => { list.reload(true); health.reload(true); }, 60_000);
    return () => clearInterval(t);
  }, [list.reload, health.reload]);

  return (
    <>
      <PageTitle sub={list.data?.company}>Incidents</PageTitle>
      <Load q={list}>
        {d => d.incidents.length === 0 ? (
          <EmptyState title="No incidents"
            action={<LinkButton href={href({ name: "health" })}>View bot health</LinkButton>}>
            {health.data ? (
              <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                The bot is <StateBadge state={health.data.learning_state} />
                {health.data.threshold !== null && <>· threshold <span className="num text-text">{num(health.data.threshold)} ± {num(health.data.threshold_margin)}</span></>}
                · last answer {ago(health.data.last_answer_at)}
              </span>
            ) : health.error ? "Bot state unavailable." : "Loading the bot's state…"}
          </EmptyState>
        ) : (
          <div className="bg-surface border border-line rounded-lg overflow-hidden" role="list" aria-label="Incidents, newest first">
            <div className="hidden md:grid grid-cols-[72px_104px_150px_110px_minmax(0,1fr)] gap-4 items-center px-5 h-10 text-xs text-subtle border-b border-line" aria-hidden>
              <span>Severity</span><span>Status</span><span>Started</span><span>Duration</span><span>Probable cause</span>
            </div>
            {d.incidents.map(i => <div role="listitem" key={i.id}><Row i={i} /></div>)}
          </div>
        )}
      </Load>
    </>
  );
}
