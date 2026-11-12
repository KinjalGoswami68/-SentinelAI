// The landing page's scroll sequence: normal answers -> the answers change -> an incident
// opens -> the investigation note writes itself, with the cited request ids highlighted.
// Data: the newest Volt incident from the key-free demo API, or the bundled snapshot when
// the API is asleep, slow, or it is outside the service's 09:00-21:00 IST window.
import { API_BASE, DEMO_NAME } from "../config";
import fallback from "./fallback-incident.json";

interface Point { t: string; request_id: string | null; distance: number | null; flagged: boolean; nonanswer: boolean;
                  prompt_version?: string | null; model_version?: string | null }
interface Version { field: "prompt_version" | "model_version"; value: string; first_seen: string }
interface Claim { text: string; evidence_ids: string[] }
interface Data {
  source: "live" | "snapshot";
  company: string;
  incident: { id: number; severity: number; severity_label: string; opened_at: string; closed_at: string | null; reasons: string[] };
  note: { cause: string; confidence: number; written_by: string;
          report: { headline: string; claims: Claim[]; example_ids: string[]; suggested_check: string } } | null;
  distance_series: { threshold: number | null; points: Point[] };
  version_timeline?: Version[];
}

const SVG = "http://www.w3.org/2000/svg";

/** API text (LLM output derived from end-user answers, company names) is never trusted as HTML. */
const esc = (s: unknown) => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const W = 640, H = 300, PAD = { l: 8, r: 8, t: 16, b: 16 };

function inServiceWindow(now = new Date()) {
  const ist = (now.getUTCHours() * 60 + now.getUTCMinutes() + 330) % 1440;
  return ist >= 9 * 60 && ist < 21 * 60;
}

async function getJSON<T>(path: string, ms: number): Promise<T> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(API_BASE + path, { signal: ctl.signal });
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()) as T;
  } finally { clearTimeout(t); }
}

async function load(): Promise<Data> {
  const snap = fallback as unknown as Data;
  if (!inServiceWindow()) return snap;                    // never wake the free instance outside its window
  try {
    const list = await getJSON<{ incidents: { id: number; cause: string | null }[] }>("/demo/volt/incidents", 4000);
    const inc = list.incidents.find(i => i.cause);
    if (!inc) return snap;
    const d = await getJSON<Omit<Data, "source">>(`/demo/volt/incidents/${inc.id}`, 4000);
    if (!d.note || d.distance_series.points.length < 10) return snap;
    return { ...d, source: "live" };
  } catch { return snap; }
}

// ---- layout of the strip ------------------------------------------------------------

/** The version that appeared last before the incident opened and after the first point
 *  (the version-regression check can open an incident with no single answer over the line). */
function newVersion(d: Data, p: Point[]): Version | null {
  const opened = Date.parse(d.incident.opened_at), first = p.length ? Date.parse(p[0].t) : Infinity;
  const vs = (d.version_timeline ?? []).filter(v => Date.parse(v.first_seen) > first && Date.parse(v.first_seen) <= opened);
  return vs.sort((a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen))[0] ?? null;
}

/** First answer of the change: from here on at least half of the next 10 carry a signal;
 *  with no signals, the first answer on the new version; else 70% of the way along. */
function changeIndex(p: Point[], v: Version | null) {
  const sig = p.map(x => x.flagged || x.nonanswer);
  for (let i = 0; i < p.length; i++) {
    const next = sig.slice(i, i + 10);
    if (sig[i] && next.filter(Boolean).length >= Math.min(5, next.length)) return i;
  }
  const iv = v ? p.findIndex(x => x[v.field] === v.value) : -1;
  return iv >= 0 ? iv : Math.floor(p.length * 0.7);
}

/** Caught as a group: a new version and no answer after it over the line. */
function groupCaught(d: Data) {
  const p = d.distance_series.points.filter(x => x.distance !== null);
  const v = newVersion(d, p);
  if (!v) return null;
  const ci = changeIndex(p, v);
  return p.slice(ci).some(x => x.flagged || x.nonanswer) ? null : v;
}

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>, parent?: Element) {
  const e = document.createElementNS(SVG, tag);
  for (const k in attrs) {
    const v = String(attrs[k]);
    if (v.startsWith("var(")) e.style.setProperty(k, v); else e.setAttribute(k, v);
  }
  parent?.appendChild(e);
  return e;
}

interface Parts { svg: SVGSVGElement; base: SVGElement[]; changed: SVGElement[]; window: SVGElement; threshold: SVGElement[] }

function strip(d: Data): Parts {
  const pts = d.distance_series.points.filter(p => p.distance !== null);
  const thr = d.distance_series.threshold ?? 0.4;
  const ymax = Math.max(thr, ...pts.map(p => p.distance!)) * 1.15;
  const x = (i: number) => PAD.l + (i / Math.max(1, pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - v / ymax) * (H - PAD.t - PAD.b);
  const ver = newVersion(d, pts);
  const ci = changeIndex(pts, ver);
  const group = groupCaught(d);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img",
    "aria-label": `${pts.length} answers over time with their distance from normal; from answer ${ci + 1} the answers change` +
      (group ? `: ${group.field} ${group.value}. No single answer crosses the threshold; as a group they moved.` : ".") }) as SVGSVGElement;
  const win = el("g", {}, svg);
  el("rect", { x: x(ci) - 4, y: PAD.t - 8, width: W - PAD.r - x(ci) + 4, height: H - PAD.t - PAD.b + 16, fill: "var(--t-bad)", "fill-opacity": 0.1, rx: 4 }, win);
  if (group) {
    const lbl = el("text", { x: x(ci) + 4, y: PAD.t + 6, fill: "var(--t-muted)", "font-size": 11, "font-family": "JetBrains Mono, monospace" }, win);
    lbl.textContent = `${group.field} ${group.value}`;
  }
  const tl = el("line", { x1: PAD.l, x2: W - PAD.r, y1: y(thr), y2: y(thr), stroke: "var(--t-subtle)", "stroke-dasharray": "4 3" }, svg);
  const tt = el("text", { x: PAD.l + 4, y: y(thr) - 6, fill: "var(--t-subtle)", "font-size": 11, "font-family": "JetBrains Mono, monospace" }, svg);
  tt.textContent = `threshold ${thr.toFixed(3)}`;
  const base: SVGElement[] = [], changed: SVGElement[] = [];
  pts.forEach((p, i) => {
    const px = x(i), py = y(p.distance!);
    let m: SVGElement;
    if (p.flagged) m = el("path", { d: `M${px} ${py - 5}L${px + 5} ${py}L${px} ${py + 5}L${px - 5} ${py}Z`, fill: "var(--t-bad)" }, svg);
    else if (p.nonanswer) m = el("circle", { cx: px, cy: py, r: 4, fill: "none", stroke: "var(--t-bad)", "stroke-width": 1.5 }, svg);
    else if (group && i >= ci) m = el("circle", { cx: px, cy: py, r: 3.5, fill: "var(--t-text)" }, svg);   // the new version's answers
    else m = el("circle", { cx: px, cy: py, r: 2.5, fill: i < ci ? "var(--t-subtle)" : "var(--t-muted)" }, svg);
    (i < ci ? base : changed).push(m);
  });
  return { svg, base, changed, window: win, threshold: [tl, tt] };
}

// ---- note with highlighted ids -------------------------------------------------------

type Seg = { text: string; id?: boolean };

function noteSegments(d: Data): Seg[] {
  const r = d.note!.report;
  const segs: Seg[] = [{ text: r.headline + "\n" }];
  for (const c of r.claims.slice(0, 3)) {
    segs.push({ text: "\n– " + c.text.replace(/\s*\((?:[A-Z]\d+(?:[,‑\- ]+[A-Z]?\d+)*)\)\.?$/, "") + " " });
    for (const id of c.evidence_ids.slice(0, 3)) segs.push({ text: id, id: true }, { text: " " });
  }
  if (r.example_ids.length) {
    segs.push({ text: "\n\nExample requests: " });
    r.example_ids.slice(0, 3).forEach((id, i) => { if (i) segs.push({ text: " " }); segs.push({ text: id, id: true }); });
  }
  return segs;
}

/** Renders segments into `box`; returns setTyped(k) that shows the first k characters. */
function typer(box: HTMLElement, segs: Seg[]) {
  box.textContent = "";
  const nodes = segs.map(s => {
    if (!s.id) { const t = document.createTextNode(""); box.appendChild(t); return t; }
    const span = document.createElement("span");
    span.className = "num text-xs rounded-sm px-1.5 py-px border border-bad/50 text-bad bg-bad-tint";
    box.appendChild(span);
    return span;
  });
  const total = segs.reduce((n, s) => n + s.text.length, 0);
  const caret = document.createElement("span");
  caret.className = "inline-block w-[7px] h-[14px] bg-muted align-[-2px] ml-px";
  caret.setAttribute("aria-hidden", "true");
  box.appendChild(caret);
  const set = (k: number) => {
    let left = Math.round(k);
    segs.forEach((s, i) => {
      const n = Math.max(0, Math.min(s.text.length, left));
      const node = nodes[i];
      if (node instanceof Text) node.data = s.text.slice(0, n);
      else { node.textContent = s.text.slice(0, n); node.style.display = n ? "" : "none"; }
      left -= s.text.length;
    });
    caret.style.display = k >= total ? "none" : "";
  };
  set(0);
  return { set, total };
}

// ---- markup --------------------------------------------------------------------------

const STEPS = [
  ["It learns what normal looks like.", "Every answer is compared with the bot's own earlier answers."],
  ["Then the answers change.", ""],
  ["One incident opens.", ""],
  ["And it explains what changed.", "One LLM call, every claim tied to evidence ids."],
];

function sourceLine(d: Data) {
  return d.source === "live"
    ? `Live from the demo: ${esc(DEMO_NAME)}, incident #${esc(d.incident.id)}.`
    : `Recorded incident from the demo bot (${esc(d.company)}), captured ${new Date(d.incident.opened_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. Shown when the live demo has no incident or is asleep.`;
}

function badge(d: Data) {
  const sev = d.incident.severity;
  const cls = sev === 1 ? "bg-sev1 text-white" : sev === 2 ? "bg-bad-tint text-bad" : "bg-warn-tint text-warn";
  return `<span class="inline-flex items-center h-5 px-2 rounded-sm text-xs font-medium font-mono ${cls}">SEV${sev}</span>
          <span class="inline-flex items-center gap-1.5 h-5 px-2 rounded-sm text-xs font-medium bg-bad-tint text-bad"><i class="size-1.5 rounded-full bg-current"></i>incident #${esc(d.incident.id)} opened</span>`;
}

function stepTexts(d: Data) {
  const s = STEPS.map(x => [...x]);
  s[1][1] = d.note?.report.claims[0]?.text.replace(/\s*\([^)]*\)\.?\s*$/, ".") ?? "Answers move away from what was normal.";
  const g = groupCaught(d);
  s[2][1] = g
    ? `No single answer crossed the line. The answers on ${g.field.replace("_", " ")} ${g.value}, taken together, did. One Slack message.`
    : `Why: ${d.incident.reasons.join("; ")}. One Slack message, not one per answer.`;
  return s;
}

function noteHeader(d: Data) {
  const n = d.note!;
  return `<div class="flex flex-wrap items-baseline gap-x-3"><b class="font-mono font-medium text-lg">${esc(n.cause)}</b>
          <span class="num text-muted text-sm">confidence ${esc(Number(n.confidence).toFixed(2))}</span></div>`;
}

export async function mount(root: HTMLElement, reduced: boolean) {
  const d = await load();
  if (reduced || !d.note) document.getElementById("seq-reserve")?.remove();   // no pin in these cases
  if (!d.note) { root.innerHTML = `<p class="text-muted">No incident to show yet.</p>`; return; }
  const steps = stepTexts(d);
  const segs = noteSegments(d);

  if (reduced) {                                         // static frames, no GSAP
    root.className = "max-w-[1120px] mx-auto px-4 sm:px-8 py-16 sm:py-24";
    root.innerHTML = `<p class="text-xs text-subtle m-0 mb-6">${sourceLine(d)}</p><ol class="list-none p-0 m-0 grid grid-cols-1 md:grid-cols-2 gap-4" id="frames"></ol>`;
    const ol = root.querySelector("#frames")!;
    steps.forEach((st, k) => {
      const li = document.createElement("li");
      li.className = "panel p-5";
      li.innerHTML = `<p class="num text-xs text-subtle m-0">0${k + 1}</p><h3 class="text-lg mt-1 mb-1">${esc(st[0])}</h3><p class="text-muted text-sm m-0 mb-4">${esc(st[1])}</p>`;
      if (k < 3) {
        const p = strip(d);
        if (k === 0) { p.changed.forEach(e => e.setAttribute("opacity", "0")); }
        if (k < 2) p.window.setAttribute("opacity", "0");
        li.appendChild(p.svg);
        if (k === 2) { const b = document.createElement("div"); b.className = "flex gap-2 mt-3"; b.innerHTML = badge(d); li.appendChild(b); }
      } else {
        const box = document.createElement("div");
        box.innerHTML = noteHeader(d);
        const body = document.createElement("p"); body.className = "text-sm whitespace-pre-wrap mt-2 mb-0";
        box.appendChild(body); li.appendChild(box);
        const t = typer(body, segs); t.set(t.total);
      }
      ol.appendChild(li);
    });
    return;
  }

  root.className = "max-w-[1120px] mx-auto px-4 sm:px-8 h-[100svh] py-6 sm:py-10 flex flex-col";
  root.innerHTML = `
    <div class="grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-4 lg:gap-10 flex-1 min-h-0 content-start lg:content-center">
      <div>
        <div class="panel p-3 sm:p-4" id="seq-chart"></div>
        <div class="flex gap-2 mt-3 h-5" id="seq-badge" style="opacity:0">${badge(d)}</div>
        <p class="text-xs text-subtle mt-3 sm:mt-4 mb-0 max-w-[60ch]">${sourceLine(d)}</p>
      </div>
      <div class="relative min-h-0">
        <div id="seq-steps" class="relative h-[92px] sm:h-[104px]"></div>
        <div id="seq-note" class="panel p-4 sm:p-5 mt-3" style="opacity:0">
          ${noteHeader(d)}
          <p id="seq-note-body" class="text-sm whitespace-pre-wrap mt-2 mb-0 min-h-[8lh]"></p>
          <p class="text-xs text-subtle mt-3 mb-0">Written by ${esc(d.note.written_by.replace(/^openai\//, ""))} · <a class="text-accent" href="app/demo/">open the demo</a></p>
        </div>
      </div>
    </div>`;
  const parts = strip(d);
  root.querySelector("#seq-chart")!.appendChild(parts.svg);
  const stepsBox = root.querySelector<HTMLElement>("#seq-steps")!;
  const stepEls = steps.map(([h, p]) => {
    const e = document.createElement("div");
    e.className = "absolute inset-0";
    e.style.opacity = "0";
    e.innerHTML = `<h3 class="text-xl sm:text-[34px] sm:leading-[1.1] tracking-[-0.03em] m-0">${esc(h)}</h3><p class="text-muted text-sm sm:text-md mt-2 mb-0">${esc(p)}</p>`;
    stepsBox.appendChild(e);
    return e;
  });
  const t = typer(root.querySelector<HTMLElement>("#seq-note-body")!, segs);
  // The start frame (everything hidden) without GSAP; GSAP and the pinned ScrollTrigger load on
  // the visitor's first scroll, touch or key. The section is already in view at load, and doing
  // that work during page load cost ~0.9 s of blocking time on mobile once the data came live.
  for (const e of [...parts.base, ...parts.changed, parts.window]) e.style.opacity = "0";
  // The pin's scroll distance is reserved in index.html (#seq-reserve, 320vh); make it exact.
  const reserve = document.getElementById("seq-reserve");
  const size = () => { if (reserve) reserve.style.height = `${PIN_VH * innerHeight}px`; };
  size();
  addEventListener("resize", size, { passive: true });     // ScrollTrigger re-measures after resizes too
  const go = () => {
    for (const ev of START_EVENTS) removeEventListener(ev, go);
    void animate(parts, stepEls, t);
  };
  if (scrollY > 0) go();
  else for (const ev of START_EVENTS) addEventListener(ev, go, { passive: true });
}

const START_EVENTS = ["scroll", "wheel", "touchstart", "keydown", "pointerdown"] as const;
const PIN_VH = 3.2;                                       // the pin's end: "+=320%" of the viewport

async function animate(parts: Parts, stepEls: HTMLElement[], t: { set: (k: number) => void; total: number }) {
  const [{ gsap }, { ScrollTrigger }] = await Promise.all([import("gsap"), import("gsap/ScrollTrigger")]);
  gsap.registerPlugin(ScrollTrigger);
  const section = document.getElementById("sequence")!;
  gsap.set([...parts.base, ...parts.changed], { opacity: 0 });
  gsap.set(parts.window, { opacity: 0 });
  const proxy = { k: 0 };
  // pinSpacing false: #seq-reserve (in the page from the first paint) is the pin's scroll distance,
  // so starting GSAP never changes the page height or the visitor's scroll position
  const tl = gsap.timeline({
    defaults: { ease: "none" },
    scrollTrigger: { trigger: section, start: "top top", end: `+=${PIN_VH * 100}%`, scrub: 1, pin: true,
                     pinSpacing: false, anticipatePin: 1 },
  });
  tl.to(stepEls[0], { opacity: 1, duration: 0.2 })
    .to(parts.base, { opacity: 1, duration: 1, stagger: { amount: 0.8 } }, "<")
    .to(stepEls[0], { opacity: 0, duration: 0.2 }, "+=0.2")
    .to(stepEls[1], { opacity: 1, duration: 0.2 })
    .to(parts.changed, { opacity: 1, duration: 0.8, stagger: { amount: 0.6 } }, "<")
    .to(stepEls[1], { opacity: 0, duration: 0.2 }, "+=0.2")
    .to(stepEls[2], { opacity: 1, duration: 0.2 })
    .to(parts.window, { opacity: 1, duration: 0.4 }, "<")
    .fromTo("#seq-badge", { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.3 }, "<")
    .to(stepEls[2], { opacity: 0, duration: 0.2 }, "+=0.3")
    .to(stepEls[3], { opacity: 1, duration: 0.2 })
    .to("#seq-note", { opacity: 1, duration: 0.2 }, "<")
    .to(proxy, { k: t.total, duration: 1.6, onUpdate: () => t.set(proxy.k) })
    .to({}, { duration: 0.3 });

  ScrollTrigger.refresh();
}
