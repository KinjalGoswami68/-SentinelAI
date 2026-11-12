import { useState, type FormEvent } from "react";
import { ApiError, askPath, getKey, isDemo, request, type AskCite, type AskResult } from "../api";
import { Rid } from "../screens/IncidentDetail";
import { AsciiSpinner, Button, Card, ErrorNotice, WakingUp } from "./ui";

const SUGGESTIONS = ["Why do you think this happened?", "When did it start?", "Show me examples of the changed answers", "What should I check first?"];

interface Turn { q: string; a?: AskResult; error?: ApiError }

/** "Ask about this incident": the agent answers from this incident's data, citing request ids.
 *  The conversation lives in this component only (nothing is stored in the browser). */
export default function AskPanel({ incidentId }: { incidentId: number }) {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const left = [...turns].reverse().find(t => t.a)?.a?.questions_left_today;

  const send = async (question: string) => {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true); setSlow(false); setQ("");
    const turn: Turn = { q: text };
    setTurns(ts => [...ts, turn]);
    try {
      turn.a = await request<AskResult>(askPath(incidentId), {
        method: "POST", body: { question: text }, key: isDemo() ? null : getKey(),
        onSlow: () => setSlow(true), timeoutMs: 150_000,
      });
    } catch (e) {
      turn.error = e instanceof ApiError ? e : new ApiError(0, String(e));
    } finally {
      setTurns(ts => ts.map(t => (t === turn ? { ...turn } : t)));
      setBusy(false); setSlow(false);
    }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void send(q); };

  return (
    <Card title="Ask about this incident">
      <p className="text-sm text-muted mt-0 mb-4 max-w-[70ch]">
        An agent looks at this incident's data with the same tools as the investigation and answers in short claims.
        Every claim cites real request ids; anything it can't back up is dropped.
        {isDemo() && " Demo: 5 questions per hour per visitor."}
      </p>

      {turns.length > 0 && (
        <ol className="list-none p-0 m-0 mb-5 space-y-5" aria-live="polite">
          {turns.map((t, i) => (
            <li key={i}>
              <p className="m-0 text-sm"><span className="label mr-2">you</span>{t.q}</p>
              {!t.a && !t.error && (
                <div className="mt-3 space-y-3">
                  <p className="m-0 text-sm text-muted font-mono"><AsciiSpinner /> checking the data…</p>
                  {slow && i === turns.length - 1 && <WakingUp />}
                </div>
              )}
              {t.error && <div className="mt-3"><ErrorNotice error={t.error} /></div>}
              {t.a && (
                <div className="mt-3 border-l-2 border-line-strong pl-4">
                  {t.a.claims.length ? (
                    <ul className="m-0 pl-5 space-y-2 list-disc marker:text-subtle">
                      {t.a.claims.map((c, k) => (
                        <li key={k}>{c.text}{" "}
                          <span className="inline-flex flex-wrap gap-1 align-middle">
                            {(c.cites ?? c.ids.map(id => ({ id, label: id, title: id, kind: /^[A-Z]\d+$/.test(id) ? "signal" : "answer" } as AskCite)))
                              .map(x => x.kind === "answer"
                                ? <Rid key={x.id} id={x.id} hot />
                                : <span key={x.id} title={x.title} className="num text-xs rounded-sm px-1.5 py-px border border-line-strong bg-raised text-muted">{x.label}</span>)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="m-0 text-muted">{t.a.note || "I couldn't back up an answer to that with this incident's data."}</p>
                  )}
                  {t.a.claims.length > 0 && t.a.note && <p className="text-sm text-muted mt-3 mb-0">{t.a.note}</p>}
                  <p className="text-xs text-subtle mt-3 mb-0">
                    {t.a.model.replace(/^openai\//, "")}
                    {t.a.tools_used.length > 0 && <> · looked at {t.a.tools_used.join(", ")}</>}
                    {t.a.dropped > 0 && <> · {t.a.dropped} unsupported claim{t.a.dropped === 1 ? "" : "s"} dropped</>}
                    {t.a.stopped && <> · stopped at the {t.a.stopped}</>}
                  </p>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 sm:items-end">
        <label className="flex-1 min-w-0">
          <span className="sr-only">Your question</span>
          <input value={q} onChange={e => setQ(e.target.value)} maxLength={500} disabled={busy}
                 placeholder="Why did this happen? Which answers changed?"
                 className="w-full h-11 px-4 rounded-[10px] bg-raised border border-control text-text placeholder:text-subtle" />
        </label>
        <Button variant="primary" type="submit" disabled={busy || !q.trim()}>{busy ? "Asking…" : "Ask"}</Button>
      </form>
      <div className="flex flex-wrap items-center gap-2 mt-3">
        {turns.length === 0 && SUGGESTIONS.map(s => (
          <Button key={s} variant="ghost" className="!h-8 text-sm" disabled={busy} onClick={() => void send(s)}>{s}</Button>
        ))}
        {left !== undefined && <span className="text-xs text-subtle ml-auto">{left} question{left === 1 ? "" : "s"} left today</span>}
      </div>
    </Card>
  );
}
