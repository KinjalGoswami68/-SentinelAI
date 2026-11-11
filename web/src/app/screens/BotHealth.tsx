import { useEffect } from "react";
import { paths, useApi, type BotHealth as Health } from "../api";
import { ago, exact, num, when } from "../format";
import { href } from "../router";
import { Card, Load, PageTitle, StatTile, StateBadge } from "../components/ui";

const STATE_TEXT = {
  learning: "Collecting answers to learn what normal looks like. Nothing is flagged yet (needs 30 answers).",
  provisional: "Answers are flagged on this dashboard, but no Slack alerts or investigations yet, until the threshold is stable and traffic spans 2 hours.",
  ready: "Detection is on: incidents open, investigations run and Slack messages are sent.",
};

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
