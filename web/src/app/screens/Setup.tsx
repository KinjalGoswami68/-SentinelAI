import { useEffect, useState, type FormEvent } from "react";
import { ApiError, getKey, request, setKey, type BotHealth, type Registered } from "../api";
import { API_BASE } from "../../config";
import { Button, Card, CodeBlock, ErrorNotice, PageTitle, WakingUp } from "../components/ui";

export const snippet = (key: string) => `import requests
requests.post("${API_BASE}/ingest", headers={"X-API-Key": "${key}"},
              json={"prompt": question, "response": answer})`;

/** Polls /bot/health with the key until the first answer arrives. */
function FirstAnswer({ apiKey }: { apiKey: string }) {
  const [h, setH] = useState<BotHealth | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    let stop = false, timer = 0;
    const poll = async () => {
      try {
        const d = await request<BotHealth>("/bot/health", { key: apiKey });
        if (stop) return;
        setH(d); setErr(null);
        if (d.answers_total > 0) return;                       // done
      } catch (e) { if (!stop) setErr(e as ApiError); }
      if (!stop) timer = window.setTimeout(poll, 5000);
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [apiKey]);

  const got = (h?.answers_total ?? 0) > 0;
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 px-4 py-3 rounded-md border border-line bg-raised text-sm">
      {got ? (
        <>
          <i className="size-2 rounded-full bg-ok shrink-0" aria-hidden />
          <span>Received: <span className="num">{h!.answers_total}</span> answer{h!.answers_total > 1 ? "s" : ""}. It now learns what normal looks like (30 answers over 2 hours).</span>
          <a href="#/health" className="ml-auto text-ok whitespace-nowrap">Bot health →</a>
        </>
      ) : (
        <>
          <span className="size-3 rounded-full border-2 border-line-strong border-t-muted animate-spin shrink-0" aria-hidden />
          <span>Waiting for your first answer…{err ? ` (${err.message})` : ""}</span>
        </>
      )}
    </div>
  );
}

export default function Setup({ onSignedIn }: { onSignedIn: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [reg, setReg] = useState<Registered | null>(null);
  const [saved, setSaved] = useState(false);
  const signedIn = !!getKey();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    if (n.length < 2) { setErr(new ApiError(400, "Name needs at least 2 characters.")); return; }
    setBusy(true); setErr(null); setSlow(false);
    try {
      setReg(await request<Registered>("/register", { method: "POST", body: { company_name: n }, onSlow: () => setSlow(true) }));
    } catch (x) { setErr(x as ApiError); } finally { setBusy(false); setSlow(false); }
  };

  return (
    <>
      <PageTitle sub="Register a bot, send one answer, and it starts learning what normal looks like.">Setup</PageTitle>
      {!reg ? (
        <Card title="1. Register your bot">
          {signedIn && <p className="text-sm text-muted mt-0 mb-4">You are signed in with a key already. Registering creates a separate bot with a new key.</p>}
          <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 sm:items-end">
            <label className="flex-1 min-w-0">
              <span className="block text-sm text-muted mb-1.5">Bot or company name</span>
              <input value={name} onChange={e => setName(e.target.value)} maxLength={100} required minLength={2}
                     autoComplete="organization" placeholder="Acme support bot"
                     className="w-full h-10 px-3 rounded-md bg-raised border border-control text-text placeholder:text-subtle" />
            </label>
            <Button variant="primary" type="submit" disabled={busy}>{busy ? "Registering…" : "Register"}</Button>
          </form>
          <p className="text-xs text-subtle mt-3 mb-0">A bot that sends no answers within 7 days is deleted.</p>
          {slow && <div className="mt-4"><WakingUp /></div>}
          {err && <div className="mt-4"><ErrorNotice error={err} /></div>}
        </Card>
      ) : (
        <div className="space-y-4">
          <Card title="1. Your API key">
            <div role="alert" className="flex gap-3 items-start px-4 py-3 rounded-md border border-[#5C4A23] bg-warn-tint text-sm mb-4">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-warn shrink-0 mt-1" aria-hidden>
                <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
              </svg>
              <span><b className="text-warn font-medium">Save this now.</b> It is shown once; only a hash is stored, so it cannot be recovered.</span>
            </div>
            <CodeBlock code={reg.api_key} label="API key" />
            <div className="flex flex-wrap gap-3 mt-4">
              {!saved
                ? <Button variant="primary" onClick={() => { setKey(reg.api_key); setSaved(true); onSignedIn(); }}>Use this key in this browser</Button>
                : <span className="text-sm text-ok self-center">Saved in this browser.</span>}
            </div>
          </Card>
          <Card title="2. Send answers from your bot">
            <p className="text-muted mt-0">After each reply your bot gives, post the question and the answer. Add <span className="num text-text">request_id</span>, <span className="num text-text">prompt_version</span> and <span className="num text-text">model_version</span> if you have them; they make the investigation notes better.</p>
            <CodeBlock code={snippet(reg.api_key)} label="Python snippet" />
          </Card>
          <Card title="3. First answer">
            <FirstAnswer apiKey={reg.api_key} />
          </Card>
        </div>
      )}
    </>
  );
}
