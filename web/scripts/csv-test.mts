// Tests for src/app/csv.ts. Run: node web/scripts/csv-test.mts (Node 24 strips the types).
import assert from "node:assert/strict";
import { parseAnswers, parseCsv, splitByTime, TEMPLATE_CSV } from "../src/app/csv.ts";

let n = 0;
const test = (name: string, fn: () => void) => { fn(); n++; console.log("ok", name); };

test("quoted commas, escaped quotes, newlines, CRLF", () => {
  const rows = parseCsv('a,b\r\n"x, y","he said ""hi"""\r\n"line1\nline2",z\r\n');
  assert.deepEqual(rows, [["a", "b"], ["x, y", 'he said "hi"'], ["line1\nline2", "z"]]);
});

test("BOM and blank lines", () => {
  assert.deepEqual(parseCsv("﻿response\n\nhello\n\n"), [["response"], ["hello"]]);
});

test("CSV with group column -> before/after", () => {
  const p = parseAnswers(TEMPLATE_CSV);
  assert.equal(p.before!.length, 2); assert.equal(p.after!.length, 1);
  assert.equal(p.before![1].response, "Yes, cash on delivery works for orders up to 10,000 rupees.");
  assert.equal(p.after![0].prompt_version, "v2"); assert.deepEqual(p.errors, []);
});

test("CSV header aliases and missing response", () => {
  const p = parseAnswers("Question,Answer,timestamp\nq1,a1,2026-11-12T10:00:00Z\nq2,,2026-11-12T10:01:00Z\n");
  assert.equal(p.all!.length, 1); assert.equal(p.all![0].prompt, "q1"); assert.equal(p.all![0].t, "2026-11-12T10:00:00Z");
  assert.equal(p.errors.length, 1);
  assert.match(parseAnswers("prompt,text\na,b\n").errors[0], /response/);
});

test("JSON shapes", () => {
  assert.equal(parseAnswers('["a", "b"]').all!.length, 2);
  const g = parseAnswers('{"before": [{"response": "x"}], "after": ["y", {"answer": "z"}]}');
  assert.equal(g.before!.length, 1); assert.equal(g.after!.length, 2);
  assert.match(parseAnswers("{nope").errors[0], /JSON/);
  assert.match(parseAnswers('{"x": 1}').errors[0], /list of answers/);
});

test("split one timestamped list", () => {
  const all = [{ response: "b", t: "2026-11-12T10:05:00Z" }, { response: "a", t: "2026-11-12T10:00:00Z" }, { response: "c", t: "2026-11-12T11:00:00Z" }];
  const s = splitByTime(all, "2026-11-12T10:30:00Z");
  assert.deepEqual(s.before.map(a => a.response), ["a", "b"]); assert.deepEqual(s.after.map(a => a.response), ["c"]);
  assert.match(splitByTime([{ response: "x" }], "2026-11-12T10:30:00Z").errors[0], /no readable time/);
});

console.log(`${n} csv tests passed`);
