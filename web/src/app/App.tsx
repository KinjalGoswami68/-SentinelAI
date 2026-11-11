import { useState, type FormEvent, type ReactNode } from "react";
import { ApiError, clearKey, getKey, isDemo, request, setKey, type BotHealth as Health } from "./api";
import { DEMO_BLURB, DEMO_NAME, PRODUCT_NAME, REPO_URL } from "../config";
import { href, useRoute, type Route } from "./router";
import { Button, Card, ErrorNotice, LinkButton, PageTitle, ThemeControls, WakingUp } from "./components/ui";
import Incidents from "./screens/Incidents";
import IncidentDetail from "./screens/IncidentDetail";
import BotHealth from "./screens/BotHealth";
import Setup from "./screens/Setup";

const BASE = import.meta.env.BASE_URL;          // "/ShiftLens/"

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [key, setK] = useState("");
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null); setSlow(false);
    try {
      await request<Health>("/bot/health", { key: key.trim(), onSlow: () => setSlow(true) });   // check it before saving
      setKey(key); onSignedIn();
    } catch (x) { setErr(x as ApiError); } finally { setBusy(false); setSlow(false); }
  };
  return (
    <>
      <PageTitle sub="Paste the API key you got when you registered. It is kept in this browser only.">Sign in</PageTitle>
      <Card>
        <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 sm:items-end">
          <label className="flex-1 min-w-0">
            <span className="block text-sm text-muted mb-1.5">API key</span>
            <input value={key} onChange={e => setK(e.target.value)} required autoComplete="off" spellCheck={false}
                   placeholder="sk-…" className="w-full h-11 px-4 rounded-[10px] bg-raised border border-control text-text font-mono text-sm placeholder:text-subtle" />
          </label>
          <Button variant="primary" type="submit" disabled={busy || !key.trim()}>{busy ? "Checking…" : "Sign in"}</Button>
        </form>
        {slow && <div className="mt-4"><WakingUp /></div>}
        {err && <div className="mt-4"><ErrorNotice error={err} /></div>}
        <p className="text-sm text-muted mt-4 mb-0">No key yet? <a href={href({ name: "setup" })} className="text-accent">Register a bot</a>, or look at the <a href={`${BASE}app/demo/`} className="text-accent">live demo</a>.</p>
      </Card>
    </>
  );
}

function NavLink({ to, cur, children }: { to: Route; cur: Route; children: ReactNode }) {
  const active = to.name === cur.name || (to.name === "incidents" && cur.name === "incident");
  return (
    <a href={href(to)} aria-current={active ? "page" : undefined}
       className={`px-3 h-9 inline-flex items-center rounded-full text-sm no-underline transition-colors duration-150 ${active ? "text-text bg-raised shadow-[inset_0_0_0_1px_var(--edge-strong)]" : "text-muted hover:text-text"}`}>
      {children}
    </a>
  );
}

export default function App() {
  const demo = isDemo();
  const route = useRoute();
  const [, bump] = useState(0);                  // re-render after sign in / out
  const signedIn = demo || !!getKey();

  const signOut = () => { clearKey(); window.location.hash = "#/"; bump(x => x + 1); };

  let screen: ReactNode;
  if (route.name === "setup" && !demo) screen = <Setup onSignedIn={() => bump(x => x + 1)} />;
  else if (!signedIn) screen = <SignIn onSignedIn={() => bump(x => x + 1)} />;
  else if (route.name === "incident") screen = <IncidentDetail id={route.id} key={route.id} />;
  else if (route.name === "health") screen = <BotHealth />;
  else screen = <Incidents />;

  return (
    <div className="min-h-dvh flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 bg-raised px-3 py-2 rounded-md">Skip to content</a>
      {demo && (
        <div className="border-b border-line">
          <p className="max-w-[1200px] mx-auto px-4 sm:px-8 py-2.5 m-0 text-sm text-muted">
            <span className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-[.12em] text-text"><i className="size-1.5 rounded-full bg-ok" aria-hidden />live demo</span>{" "}
            <span className="ml-1">{DEMO_NAME}, {DEMO_BLURB}.</span> <a href={`${BASE}app/#/setup`} className="text-accent whitespace-nowrap">Monitor your own bot →</a>
          </p>
        </div>
      )}
      <header className="border-b border-line sticky top-0 z-30" style={{ background: "color-mix(in oklab, var(--t-bg) 92%, transparent)" }}>
        <div className="max-w-[1200px] mx-auto px-4 sm:px-8 flex flex-wrap items-center gap-x-6 py-2 sm:py-0 sm:h-16">
          <a href={BASE} className="inline-flex items-center gap-2.5 font-display font-semibold text-[15px] tracking-tight text-text no-underline whitespace-nowrap h-10">
            <svg viewBox="0 0 32 32" width="24" height="24" aria-hidden><circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="3" /><circle cx="16" cy="16" r="5" fill="currentColor" /><path d="M16 3v5M16 24v5M3 16h5M24 16h5" stroke="currentColor" strokeWidth="2" /></svg>
            {PRODUCT_NAME}
          </a>
          {signedIn && (
            <nav aria-label="Main" className="order-last sm:order-none basis-full sm:basis-auto flex items-center gap-1 -mx-3 sm:mx-0">
              <NavLink to={{ name: "incidents" }} cur={route}>Incidents</NavLink>
              <NavLink to={{ name: "health" }} cur={route}>Bot health</NavLink>
              {!demo && <NavLink to={{ name: "setup" }} cur={route}>Setup</NavLink>}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-2">
            {!demo && signedIn && <Button variant="ghost" onClick={signOut}>Sign out</Button>}
            {demo && <LinkButton variant="ghost" href={REPO_URL} className="hidden sm:inline-flex">GitHub</LinkButton>}
            <ThemeControls />
          </div>
        </div>
      </header>
      <main id="main" className="flex-1 w-full max-w-[1200px] mx-auto px-4 sm:px-8 py-10 sm:py-14">
        <div className="rise" key={route.name === "incident" ? `i${route.id}` : route.name}>{screen}</div>
      </main>
      <footer className="border-t border-line">
        <div className="max-w-[1200px] mx-auto px-4 sm:px-8 py-6 flex flex-wrap gap-x-6 gap-y-2 text-xs text-subtle">
          <a href={REPO_URL} className="text-subtle hover:text-text">GitHub</a>
          <span>MIT licence</span>
        </div>
      </footer>
    </div>
  );
}
