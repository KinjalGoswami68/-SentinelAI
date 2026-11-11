import { useState, type FormEvent, type ReactNode } from "react";
import { ApiError, clearKey, getKey, isDemo, request, setKey, type BotHealth as Health } from "./api";
import { DEMO_BLURB, DEMO_NAME, PRODUCT_NAME, REPO_URL } from "../config";
import { href, useRoute, type Route } from "./router";
import { Button, Card, ErrorNotice, LinkButton, PageTitle, WakingUp } from "./components/ui";
import Incidents from "./screens/Incidents";
import IncidentDetail from "./screens/IncidentDetail";
import BotHealth from "./screens/BotHealth";

const BASE = import.meta.env.BASE_URL;          // "/-SentinelAI/"

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
                   placeholder="sk-…" className="w-full h-10 px-3 rounded-md bg-raised border border-control text-text font-mono text-sm placeholder:text-subtle" />
          </label>
          <Button variant="primary" type="submit" disabled={busy || !key.trim()}>{busy ? "Checking…" : "Sign in"}</Button>
        </form>
        {slow && <div className="mt-4"><WakingUp /></div>}
        {err && <div className="mt-4"><ErrorNotice error={err} /></div>}
        <p className="text-sm text-muted mt-4 mb-0">No key yet? <a href={href({ name: "setup" })} className="text-ok">Register a bot</a>, or look at the <a href={`${BASE}app/demo/`} className="text-ok">live demo</a>.</p>
      </Card>
    </>
  );
}

function NavLink({ to, cur, children }: { to: Route; cur: Route; children: ReactNode }) {
  const active = to.name === cur.name || (to.name === "incidents" && cur.name === "incident");
  return (
    <a href={href(to)} aria-current={active ? "page" : undefined}
       className={`px-3 h-9 inline-flex items-center rounded-md text-sm no-underline transition-colors duration-150 ${active ? "text-text bg-raised" : "text-muted hover:text-text"}`}>
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
  if (!signedIn) screen = <SignIn onSignedIn={() => bump(x => x + 1)} />;
  else if (route.name === "incident") screen = <IncidentDetail id={route.id} key={route.id} />;
  else if (route.name === "health") screen = <BotHealth />;
  else screen = <Incidents />;

  return (
    <div className="min-h-dvh flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 bg-raised px-3 py-2 rounded-md">Skip to content</a>
      {demo && (
        <div className="border-b border-line bg-surface">
          <p className="max-w-[1200px] mx-auto px-4 sm:px-8 py-2.5 m-0 text-sm text-muted">
            <span className="inline-flex items-center gap-2 text-text font-medium"><i className="size-1.5 rounded-full bg-ok" aria-hidden />Live demo:</span>{" "}
            {DEMO_NAME}, {DEMO_BLURB}. <a href={`${BASE}app/#/setup`} className="text-ok whitespace-nowrap">Monitor your own bot →</a>
          </p>
        </div>
      )}
      <header className="border-b border-line">
        <div className="max-w-[1200px] mx-auto px-4 sm:px-8 flex flex-wrap items-center gap-x-6 py-2 sm:py-0 sm:h-14">
          <a href={BASE} className="font-semibold text-text no-underline whitespace-nowrap h-10 inline-flex items-center">{PRODUCT_NAME}</a>
          {signedIn && (
            <nav aria-label="Main" className="order-last sm:order-none basis-full sm:basis-auto flex items-center gap-1 -mx-3 sm:mx-0">
              <NavLink to={{ name: "incidents" }} cur={route}>Incidents</NavLink>
              <NavLink to={{ name: "health" }} cur={route}>Bot health</NavLink>
            </nav>
          )}
          <div className="ml-auto flex items-center gap-2">
            {!demo && signedIn && <Button variant="ghost" onClick={signOut} className="h-9 text-sm">Sign out</Button>}
            {demo && <LinkButton variant="ghost" href={REPO_URL} className="h-9 text-sm">GitHub</LinkButton>}
          </div>
        </div>
      </header>
      <main id="main" className="flex-1 w-full max-w-[1200px] mx-auto px-4 sm:px-8 py-8 sm:py-10">{screen}</main>
      <footer className="border-t border-line">
        <div className="max-w-[1200px] mx-auto px-4 sm:px-8 py-6 flex flex-wrap gap-x-6 gap-y-2 text-xs text-subtle">
          <a href={REPO_URL} className="text-subtle hover:text-text">GitHub</a>
          <span>MIT licence</span>
        </div>
      </footer>
    </div>
  );
}
