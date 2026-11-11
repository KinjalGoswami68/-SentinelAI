// Theme: light/dark + intensity (0..1), stored in localStorage and applied before first
// paint by the inline script in each HTML page (see THEME_BOOT). Shared by landing and app.

export const KEYS = { theme: "sentinel.theme", intensity: "sentinel.intensity" } as const;
export const THEME_EVENT = "sentinel:theme";

/** Inline in <head>: same logic, no flash of the wrong theme. Keep in sync with readStored(). */
export const THEME_BOOT = `(function(){try{var d=document.documentElement,t=localStorage.getItem("${KEYS.theme}"),i=localStorage.getItem("${KEYS.intensity}");if(t==="light"||t==="dark")d.dataset.theme=t;else d.dataset.theme="dark";if(i!==null&&!isNaN(+i))d.style.setProperty("--i",String(Math.min(1,Math.max(0,+i))));}catch(e){document.documentElement.dataset.theme="dark";}})();`;

const root = () => document.documentElement;
const store = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

export function currentIntensity(): number {
  const v = parseFloat(getComputedStyle(root()).getPropertyValue("--i"));
  return Number.isFinite(v) ? v : 0.5;
}
export const isLight = () => root().dataset.theme === "light";

function emit() { window.dispatchEvent(new CustomEvent(THEME_EVENT)); }

export function setTheme(light: boolean) {
  root().dataset.theme = light ? "light" : "dark";
  store(KEYS.theme, root().dataset.theme!);
  emit();
}
export function setIntensity(v: number) {
  const x = Math.min(1, Math.max(0, v));
  root().style.setProperty("--i", String(x));
  store(KEYS.intensity, String(x));
  emit();
}

const SUN = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg>';
const MOON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>';

/** Renders the toggle + intensity fader into `host`. `dock`: vertical fader at the right edge on desktop. */
export function mountThemeControls(host: HTMLElement, opts: { dock?: boolean } = {}) {
  host.classList.add("tc");
  host.innerHTML = `
    <button type="button" class="tc-toggle"></button>
    <label class="tc-fader${opts.dock ? " dock" : ""}">
      <span class="lbl">intensity</span>
      <input type="range" min="0" max="100" step="1" aria-label="Theme intensity">
      <span class="val" aria-hidden="true"></span>
    </label>`;
  const btn = host.querySelector<HTMLButtonElement>(".tc-toggle")!;
  const range = host.querySelector<HTMLInputElement>("input")!;
  const val = host.querySelector<HTMLSpanElement>(".val")!;
  const sync = () => {
    const light = isLight();
    btn.innerHTML = light ? MOON : SUN;
    btn.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
    const n = Math.round(currentIntensity() * 100);
    range.value = String(n); val.textContent = String(n);
    range.setAttribute("aria-valuetext", `${n} percent`);
  };
  btn.addEventListener("click", () => setTheme(!isLight()));
  range.addEventListener("input", () => setIntensity(Number(range.value) / 100));
  window.addEventListener(THEME_EVENT, sync);
  sync();
  return () => window.removeEventListener(THEME_EVENT, sync);
}

/** Reflections follow the pointer: one CSS variable pair, updated at most once per frame. */
export function trackPointer() {
  let x = innerWidth / 2, y = innerHeight / 3, raf = 0;
  const frame = () => {
    raf = 0;
    root().style.setProperty("--mx", `${(x / innerWidth * 100).toFixed(1)}%`);
    root().style.setProperty("--my", `${(y / innerHeight * 100).toFixed(1)}%`);
  };
  addEventListener("pointermove", e => { x = e.clientX; y = e.clientY; if (!raf) raf = requestAnimationFrame(frame); }, { passive: true });
}

/** Edge light on .panel.hot and the highlight on .btn-chrome follow the pointer inside them. */
export function trackSurfaces(scope: ParentNode = document) {
  scope.addEventListener("pointermove", e => {
    const el = (e.target as Element).closest?.(".panel.hot, .btn-chrome") as HTMLElement | null;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const px = `${((e as PointerEvent).clientX - r.left) / r.width * 100}%`, py = `${((e as PointerEvent).clientY - r.top) / r.height * 100}%`;
    el.style.setProperty("--px", px); el.style.setProperty("--py", py); el.style.setProperty("--bx", px); el.style.setProperty("--by", py);
  }, { passive: true } as AddEventListenerOptions);
  scope.addEventListener("click", e => {
    const b = (e.target as Element).closest?.(".btn") as HTMLElement | null;
    if (!b) return;
    b.classList.remove("pulse"); void b.offsetWidth; b.classList.add("pulse");
  });
}
