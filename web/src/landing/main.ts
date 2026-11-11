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
if (fine) {
  const start = () => import("./liquid").then(m => { if (!m.startLiquid(canvas, !reduced)) staticBg(); });
  if (document.readyState === "complete") setTimeout(start, 0); else addEventListener("load", () => setTimeout(start, 0));
} else staticBg();

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
    const t = (e.target as Element).closest?.("a, button, input[type=range], .sticker");
    c.classList.toggle("on-link", !!t);
    if (t) tag.textContent = t.matches(".sticker") ? "[zoom]" : t.matches("input") ? "[drag]" : t.tagName === "A" ? "[open]" : "[press]";
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

// ---- stickers: parallax, click to enlarge (native dialog: Esc and click close it) ----
const stickers = [...document.querySelectorAll<HTMLElement>(".sticker")];
if (fine && !reduced) addEventListener("pointermove", e => {
  const dx = e.clientX / innerWidth - 0.5, dy = e.clientY / innerHeight - 0.5;
  stickers.forEach((s, k) => {
    const d = k % 2 ? 14 : 24;
    s.style.setProperty("--sx", `${(dx * d).toFixed(1)}px`); s.style.setProperty("--sy", `${(dy * d).toFixed(1)}px`);
  });
}, { passive: true });
const dlg = document.getElementById("zoom") as HTMLDialogElement, zf = document.getElementById("zoomFace")!;
stickers.forEach(s => s.addEventListener("click", () => {
  zf.replaceChildren(s.querySelector(".face")!.cloneNode(true));
  dlg.setAttribute("aria-label", s.getAttribute("aria-label") ?? "Sticker");
  dlg.showModal();
}));
dlg.addEventListener("click", () => dlg.close());

// ---- brand word: ASCII decode once (the real text is in the HTML from the start) ----
const brand = document.getElementById("brand")!;
if (!reduced) {
  const final = brand.dataset.text ?? brand.textContent ?? "", glyphs = "#%&*+=-:./|<>[]01", t0 = performance.now();
  const dec = (now: number) => {
    const p = Math.min(1, (now - t0) / 700), n = Math.floor(p * final.length);
    let out = final.slice(0, n);
    for (let k = n; k < final.length; k++) out += glyphs[(Math.random() * glyphs.length) | 0];
    brand.textContent = out;
    if (p < 1) requestAnimationFrame(dec); else brand.textContent = final;
  };
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
