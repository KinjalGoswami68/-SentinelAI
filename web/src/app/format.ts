const IST = "Asia/Kolkata";

/** "11 Nov 15:42" in the viewer's own time zone. */
export function when(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Full timestamp for a title attribute. */
export function exact(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.toLocaleString("en-GB", { hour12: false })} (your time) · ${d.toLocaleString("en-GB", { timeZone: IST, hour12: false })} IST · ${d.toISOString()}`;
}

/** "3 min ago", "2 h ago", "4 days ago". */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** "18 min", "1 h 12 min", "2 days 3 h". */
export function duration(fromIso: string, toIso: string | null, now = Date.now()): string {
  const m = Math.max(0, Math.round(((toIso ? new Date(toIso).getTime() : now) - new Date(fromIso).getTime()) / 60000));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h${m % 60 ? ` ${m % 60} min` : ""}`;
  return `${Math.floor(h / 24)} days ${h % 24} h`;
}

export const num = (x: number | null | undefined, d = 3) => (x === null || x === undefined ? "-" : x.toFixed(d));

/** prompt_change -> "prompt change" for prose; ids stay mono elsewhere. */
export const words = (s: string) => s.replace(/_/g, " ");
