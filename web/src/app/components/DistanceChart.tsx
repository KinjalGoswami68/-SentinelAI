import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { SeriesPoint } from "../api";
import { when } from "../format";

const C = { line: "#A1A1AA", dot: "#8A8A93", bad: "#FF5C5C", grid: "#27272A", axis: "#8A8A93", ver: "#A1A1AA" };

const pad = (n: number) => String(n).padStart(2, "0");
/** 24 h tick labels; seconds only when ticks are under a minute apart. */
const ticks = (v: number[]) => {
  const secs = v.length > 1 && v[1] - v[0] < 60;
  return v.map(s => { const d = new Date(s * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}${secs ? `:${pad(d.getSeconds())}` : ""}`; });
};

export interface VersionMark { t: number; label: string }

interface Props {
  points: SeriesPoint[];
  threshold: number | null;
  windowStart?: string | null;   // incident opened
  windowEnd?: string | null;     // incident closed (null = still open: shade to the end)
}

/** Version changes inside the series: wherever prompt_version or model_version differs from the previous answer. */
export function versionMarks(points: SeriesPoint[]): VersionMark[] {
  const out: VersionMark[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    if (b.prompt_version && a.prompt_version && b.prompt_version !== a.prompt_version)
      out.push({ t: Date.parse(b.t) / 1000, label: `prompt ${b.prompt_version}` });
    if (b.model_version && a.model_version && b.model_version !== a.model_version)
      out.push({ t: Date.parse(b.t) / 1000, label: `model ${b.model_version}` });
  }
  return out;
}

export function chartSummary(points: SeriesPoint[], threshold: number | null, marks: VersionMark[]): string {
  const n = points.length, f = points.filter(p => p.flagged).length, r = points.filter(p => p.nonanswer).length;
  if (!n) return "No answers in this time range.";
  const v = marks.length ? ` Version changes: ${marks.map(m => `${m.label} at ${when(new Date(m.t * 1000).toISOString())}`).join(", ")}.` : "";
  return `${n} answers from ${when(points[0].t)} to ${when(points[n - 1].t)}; ${f} were past the threshold` +
    `${threshold !== null ? ` of ${threshold.toFixed(3)}` : ""}` +
    `${r ? `; ${r} ${r === 1 ? "was a refusal or empty answer" : "were refusals or empty answers"}` : ""}.${v}`;
}

export default function DistanceChart({ points, threshold, windowStart, windowEnd }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const marks = useMemo(() => versionMarks(points), [points]);

  useEffect(() => {
    const el = box.current;
    if (!el || !points.length) return;
    const xs = points.map(p => Date.parse(p.t) / 1000);
    const ys = points.map(p => p.distance);
    const flagged = points.map(p => !!p.flagged);
    const nonans = points.map(p => !!p.nonanswer);
    const ws = windowStart ? Date.parse(windowStart) / 1000 : null;
    const we = windowEnd ? Date.parse(windowEnd) / 1000 : xs[xs.length - 1];
    const ymax = Math.max(threshold ?? 0, ...ys.map(y => y ?? 0)) * 1.15 || 1;
    const height = () => (el.clientWidth < 640 ? 240 : 260);
    const dpr = () => window.devicePixelRatio || 1;

    const draw = (u: uPlot) => {
      const ctx = u.ctx, r = dpr();
      const X = (v: number) => u.valToPos(v, "x", true), Y = (v: number) => u.valToPos(v, "y", true);
      const { top, height: h, left, width } = u.bbox;
      ctx.save();
      ctx.textAlign = "left";                                  // uPlot leaves it "right" after the y axis
      if (ws !== null) {                                       // incident window
        const x0 = Math.max(left, X(ws)), x1 = Math.min(left + width, X(we));
        if (x1 > x0) { ctx.fillStyle = "rgba(255,92,92,0.08)"; ctx.fillRect(x0, top, x1 - x0, h); }
      }
      ctx.setLineDash([3 * r, 2 * r]); ctx.lineWidth = r; ctx.strokeStyle = C.ver;
      ctx.font = `${11 * r}px "JetBrains Mono", monospace`; ctx.fillStyle = C.ver; ctx.textBaseline = "top";
      for (const m of marks) {                                 // version changes
        const x = X(m.t);
        if (x < left || x > left + width) continue;
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + h); ctx.stroke();
        const tw = ctx.measureText(m.label).width;
        ctx.fillText(m.label, x + 6 * r + tw > left + width ? x - 6 * r - tw : x + 6 * r, top + 4 * r);
      }
      if (threshold !== null) {                                // threshold
        ctx.setLineDash([4 * r, 3 * r]); ctx.strokeStyle = C.dot;
        const y = Y(threshold);
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + width, y); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = C.axis; ctx.textBaseline = "bottom";
        ctx.fillText(`threshold ${threshold.toFixed(3)}`, left + 6 * r, y - 4 * r);
      }
      ctx.setLineDash([]);
      for (let i = 0; i < xs.length; i++) {                    // answers: dot, flagged = red diamond
        const y = ys[i];
        if (y === null) continue;
        const px = X(xs[i]), py = Y(y);
        if (flagged[i]) {
          const s = 5 * r;
          ctx.fillStyle = C.bad; ctx.beginPath();
          ctx.moveTo(px, py - s); ctx.lineTo(px + s, py); ctx.lineTo(px, py + s); ctx.lineTo(px - s, py); ctx.closePath(); ctx.fill();
        } else if (nonans[i]) {                                // refusal / empty: red ring
          ctx.strokeStyle = C.bad; ctx.lineWidth = 1.5 * r; ctx.beginPath(); ctx.arc(px, py, 4 * r, 0, Math.PI * 2); ctx.stroke();
        } else {
          ctx.fillStyle = C.dot; ctx.beginPath(); ctx.arc(px, py, 2.5 * r, 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.restore();
    };

    const axis = { stroke: C.axis, grid: { stroke: C.grid, width: 1 }, ticks: { show: false },
                   font: '11px "JetBrains Mono", monospace' };
    const u = new uPlot({
      width: el.clientWidth, height: height(),
      legend: { show: false }, cursor: { show: false }, select: { show: false, left: 0, top: 0, width: 0, height: 0 },
      scales: { x: { time: true }, y: { range: [0, ymax] } },
      axes: [{ ...axis, space: 80, values: (_u, v) => ticks(v) }, { ...axis, size: 44, values: (_u, v) => v.map(x => x.toFixed(1)) }],
      series: [{}, { stroke: "rgba(161,161,170,0.45)", width: 1.25, points: { show: false }, spanGaps: true }],
      hooks: { draw: [draw] },
    }, [xs, ys], el);

    const ro = new ResizeObserver(() => u.setSize({ width: el.clientWidth, height: height() }));
    ro.observe(el);
    return () => { ro.disconnect(); u.destroy(); };
  }, [points, threshold, windowStart, windowEnd, marks]);

  return (
    <figure className="m-0">
      <p className="text-sm text-muted mt-0 mb-4">{chartSummary(points, threshold, marks)}</p>
      <div ref={box} className="w-full" role="img" aria-label="Distance from normal over time. The text above summarises it." />
      <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-subtle mt-3">
        <span className="inline-flex items-center gap-1.5"><svg width="10" height="10" aria-hidden><circle cx="5" cy="5" r="2.5" fill={C.dot} /></svg>answer</span>
        <span className="inline-flex items-center gap-1.5"><svg width="12" height="12" aria-hidden><path d="M6 1 11 6 6 11 1 6z" fill={C.bad} /></svg>flagged answer</span>
        <span className="inline-flex items-center gap-1.5"><svg width="12" height="12" aria-hidden><circle cx="6" cy="6" r="4" fill="none" stroke={C.bad} strokeWidth="1.5" /></svg>refusal or empty</span>
        <span className="inline-flex items-center gap-1.5"><svg width="18" height="10" aria-hidden><line x1="0" y1="5" x2="18" y2="5" stroke={C.dot} strokeDasharray="4 3" /></svg>threshold</span>
        {windowStart && <span className="inline-flex items-center gap-1.5"><svg width="14" height="10" aria-hidden><rect width="14" height="10" fill={C.bad} opacity=".15" /></svg>incident</span>}
        <span className="inline-flex items-center gap-1.5"><svg width="10" height="12" aria-hidden><line x1="5" y1="0" x2="5" y2="12" stroke={C.ver} strokeDasharray="3 2" /></svg>version change</span>
      </figcaption>
    </figure>
  );
}
