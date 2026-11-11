import "../styles.css";

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Copy button on the install snippet.
document.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const el = document.getElementById(btn.dataset.copy!);
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.innerText);
      btn.textContent = "copied";
      const live = btn.parentElement?.querySelector("[data-copied]");
      if (live) live.textContent = "Snippet copied";
      setTimeout(() => (btn.textContent = "copy"), 2000);
    } catch { /* clipboard blocked */ }
  });
});

// The scroll sequence (and GSAP with it) loads only when it comes near the viewport.
const root = document.getElementById("seq-root");
if (root) {
  const io = new IntersectionObserver(entries => {
    if (!entries.some(e => e.isIntersecting)) return;
    io.disconnect();
    import("./sequence").then(m => m.mount(root, reduced));
  }, { rootMargin: "100% 0px" });
  io.observe(root);
}
