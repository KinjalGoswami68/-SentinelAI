import "../styles.css";
import "./landing.css";
import { mountThemeControls, trackPointer, trackSurfaces } from "../theme";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fine = matchMedia("(pointer: fine)").matches;

mountThemeControls(document.getElementById("theme-controls")!, { dock: true });
trackPointer();
trackSurfaces();

// ---- background: WebGL on mouse/trackpad devices, after load; static gradient otherwise ----
const canvas = document.getElementById("liquid") as HTMLCanvasElement;
const staticBg = () => { canvas.style.background = "radial-gradient(1200px 700px at 72% 18%, var(--bg-hi), var(--t-bg))"; };
staticBg();
if (fine && !reduced) {
  // Start the shader on the first real mouse movement (never at load), so it costs nothing
  // for visitors who only scroll, and the static gradient stays where WebGL is slow or missing.
  const start = () => {
    removeEventListener("pointermove", start);
    import("./liquid").then(m => { if (m.startLiquid(canvas, true)) canvas.style.background = ""; });
  };
  addEventListener("pointermove", start, { passive: true });
}

// ---- ASCII cursor (landing only, fine pointers) ----
if (fine) {
  const c = document.getElementById("cur")!, tag = c.querySelector(".tag")!;
  document.documentElement.classList.add("cur-on");
  let cx = innerWidth / 2, cy = innerHeight / 2, tx = cx, ty = cy, run = false;
  const loop = () => {
    cx += (tx - cx) * (reduced ? 1 : 0.35); cy += (ty - cy) * (reduced ? 1 : 0.35);
    c.style.transform = `translate3d(${cx}px,${cy}px,0)`;
    run = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.3;
    if (run) requestAnimationFrame(loop);
  };
  addEventListener("pointermove", e => {
    tx = e.clientX; ty = e.clientY;
    if (!run) { run = true; requestAnimationFrame(loop); }
    const t = (e.target as Element).closest?.("a, button, input[type=range]");
    c.classList.toggle("on-link", !!t);
    if (t) tag.textContent = t.matches("input") ? "[drag]" : t.tagName === "A" ? "[open]" : "[press]";
  }, { passive: true });
  addEventListener("pointerdown", () => c.classList.add("down"));
  addEventListener("pointerup", () => c.classList.remove("down"));
  document.addEventListener("pointerleave", () => { c.style.opacity = "0"; });
  document.addEventListener("pointerenter", () => { c.style.opacity = "1"; });
}

// ---- magnetic: the one primary CTA (skill preset: 1-2 per screen) ----
if (fine && !reduced) document.querySelectorAll<HTMLElement>(".magnetic").forEach(el => {
  el.addEventListener("pointermove", e => {
    const r = el.getBoundingClientRect();
    el.style.setProperty("--tx", `${((e.clientX - r.left - r.width / 2) * 0.25).toFixed(1)}px`);
    el.style.setProperty("--ty", `${((e.clientY - r.top - r.height / 2) * 0.35).toFixed(1)}px`);
  });
  el.addEventListener("pointerleave", () => { el.style.setProperty("--tx", "0px"); el.style.setProperty("--ty", "0px"); });
});

// ---- brand word: ASCII decode once (the real text is in the HTML from the start) ----
// Mouse/trackpad only (skipped on touch and reduced motion), decoded in plain colour: a gradient
// clipped to 184 px text is costly to rasterise, so the chrome is applied once, at the end.
const brand = document.getElementById("brand")!;
if (!reduced && fine) {
  const final = brand.dataset.text ?? brand.textContent ?? "", glyphs = "#%&*+=-:./|<>[]01", t0 = performance.now();
  const dec = (now: number) => {
    const p = Math.min(1, (now - t0) / 700), n = Math.floor(p * final.length);
    let out = final.slice(0, n);
    for (let k = n; k < final.length; k++) out += glyphs[(Math.random() * glyphs.length) | 0];
    brand.textContent = out;
    if (p < 1) requestAnimationFrame(dec); else { brand.textContent = final; brand.classList.add("chrome-text"); }
  };
  brand.classList.remove("chrome-text");
  requestAnimationFrame(dec);
}

// ---- reveal once on scroll ----
const io = new IntersectionObserver(es => es.forEach(e => {
  if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
}), { rootMargin: "0px 0px -8% 0px" });
document.querySelectorAll(".rv").forEach(el => io.observe(el));

// ---- copy button on the install snippet ----
document.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const el = document.getElementById(btn.dataset.copy!);
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.innerText);
      btn.textContent = "copied";
      const live = btn.closest(".term")?.querySelector("[data-copied]");
      if (live) live.textContent = "Snippet copied";
      setTimeout(() => (btn.textContent = "copy"), 2000);
    } catch { /* clipboard blocked */ }
  });
});

// ---- demo video: nothing loads until the visitor clicks (preload="none" + poster) ----
const video = document.getElementById("demo-video") as HTMLVideoElement | null;
const play = document.getElementById("demo-play");
if (video && play) {
  play.addEventListener("click", () => { void video.play(); video.focus(); });
  video.addEventListener("play", () => play.remove(), { once: true });
}

// ---- the scroll sequence (and GSAP) loads only when it comes near the viewport ----
const root = document.getElementById("seq-root");
if (root) {
  const seqIo = new IntersectionObserver(entries => {
    if (!entries.some(e => e.isIntersecting)) return;
    seqIo.disconnect();
    import("./sequence").then(m => m.mount(root, reduced));
  }, { rootMargin: "100% 0px" });
  seqIo.observe(root);
}
