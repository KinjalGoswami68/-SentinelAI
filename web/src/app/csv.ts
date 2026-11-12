// Parsing for "try without code": CSV (RFC 4180: quotes, "" escapes, commas and newlines
// inside quotes, CRLF, a UTF-8 BOM) or JSON. Pure functions, no DOM: tested by
// web/scripts/csv-test.mts (node, no dependencies).

export interface Answer { response: string; prompt?: string; request_id?: string; t?: string; prompt_version?: string; model_version?: string }
export interface Parsed { before?: Answer[]; after?: Answer[]; all?: Answer[]; errors: string[] }

const FIELD_NAMES: Record<string, keyof Answer | "group"> = {
  response: "response", answer: "response", output: "response",
  prompt: "prompt", question: "prompt", input: "prompt",
  request_id: "request_id", id: "request_id",
  t: "t", time: "t", timestamp: "t", created_at: "t",
  prompt_version: "prompt_version", model_version: "model_version",
  group: "group",
};

export function parseCsv(text: string): string[][] {
  const s = text.replace(/^﻿/, "");
  const rows: string[][] = [];
  let row: string[] = [], field = "", quoted = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quoted) {
      if (c === '"') {
        if (s[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
    } else if (c === '"' && field === "") quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some(f => f !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some(f => f !== "")) rows.push(row);
  return rows;
}

function toAnswer(obj: Record<string, unknown>): (Answer & { group?: string }) | null {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    const name = FIELD_NAMES[k.trim().toLowerCase()];
    if (!name || v === null || v === undefined || v === "") continue;
    out[name] = String(v).trim();
  }
  return out.response ? (out as unknown as Answer & { group?: string }) : null;
}

function fromRecords(records: Record<string, unknown>[] | unknown[], errors: string[]): Parsed {
  const all: (Answer & { group?: string })[] = [];
  records.forEach((r, i) => {
    const a = typeof r === "string" ? (r.trim() ? { response: r.trim() } : null) : r && typeof r === "object" ? toAnswer(r as Record<string, unknown>) : null;
    if (a) all.push(a); else errors.push(`row ${i + 1}: no response text`);
  });
  if (all.some(a => a.group)) {
    const g = (a: { group?: string }) => (a.group ?? "").toLowerCase();
    const strip = ({ group: _g, ...a }: Answer & { group?: string }) => a;
    const before = all.filter(a => g(a) === "before").map(strip), after = all.filter(a => g(a) === "after").map(strip);
    const other = all.filter(a => g(a) !== "before" && g(a) !== "after").length;
    if (other) errors.push(`${other} row(s) have a group other than "before" or "after"`);
    return { before, after, errors };
  }
  return { all: all.map(({ group: _g, ...a }) => a), errors };
}

/** Text of a pasted or uploaded file -> answers. JSON: [..], {"before": [..], "after": [..]}. CSV: header row. */
export function parseAnswers(text: string): Parsed {
  const errors: string[] = [];
  const t = text.replace(/^﻿/, "").trim();
  if (!t) return { errors: ["nothing to read"] };
  if (t[0] === "[" || t[0] === "{") {
    let data: unknown;
    try { data = JSON.parse(t); } catch { return { errors: ["the JSON could not be read (check commas and quotes)"] }; }
    if (Array.isArray(data)) return fromRecords(data, errors);
    if (data && typeof data === "object") {
      const o = data as Record<string, unknown>;
      if (Array.isArray(o.before) || Array.isArray(o.after)) {
        const b = fromRecords((o.before as unknown[]) ?? [], errors), a = fromRecords((o.after as unknown[]) ?? [], errors);
        return { before: b.all ?? [], after: a.all ?? [], errors };
      }
      if (Array.isArray(o.answers)) return fromRecords(o.answers as unknown[], errors);
    }
    return { errors: ['JSON must be a list of answers, or {"before": [...], "after": [...]}'] };
  }
  const rows = parseCsv(t);
  if (rows.length < 2) return { errors: ["the CSV needs a header row and at least one answer"] };
  const header = rows[0].map(h => h.trim().toLowerCase());
  if (!header.some(h => FIELD_NAMES[h] === "response"))
    return { errors: ['the CSV needs a "response" column (or "answer" / "output")'] };
  const recs = rows.slice(1).map(r => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
  return fromRecords(recs, errors);
}

/** One timestamped list -> before / after at `splitAt` (ISO or datetime-local). */
export function splitByTime(all: Answer[], splitAt: string): { before: Answer[]; after: Answer[]; errors: string[] } {
  const cut = Date.parse(splitAt);
  if (!Number.isFinite(cut)) return { before: [], after: [], errors: ["pick the time the change happened"] };
  const missing = all.filter(a => !a.t || !Number.isFinite(Date.parse(a.t))).length;
  if (missing) return { before: [], after: [], errors: [`${missing} answer(s) have no readable time in "t"`] };
  const sorted = [...all].sort((a, b) => Date.parse(a.t!) - Date.parse(b.t!));
  return { before: sorted.filter(a => Date.parse(a.t!) < cut), after: sorted.filter(a => Date.parse(a.t!) >= cut), errors: [] };
}

/** Example file for the template download. */
export const TEMPLATE_CSV = [
  "group,prompt,response,prompt_version",
  'before,Can I return my phone after 10 days?,"Yes, phones can be returned within 30 days of delivery.",v1',
  'before,Is COD available?,"Yes, cash on delivery works for orders up to 10,000 rupees.",v1',
  'after,Can I return my phone after 10 days?,"Winter Sale! Use VOLT15 for 15% off. Yes, within 30 days.",v2',
].join("\n") + "\n";
