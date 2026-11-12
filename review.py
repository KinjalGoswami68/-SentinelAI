"""
Baseline review (M14 step 5). When a bot becomes ready, the answers it learned from are frozen
as "normal". Before that settles, check them with the day-1 checks (quality.py) and a few
spot-checks (spotcheck.py, same daily budget) and say plainly what looks off, so a bad start
is not silently learned as normal ("12% of the answers it learned from are refusals. Intended?").

`build()` is pure (tests/test_review.py); `run()` loads, checks, stores, notifies.
"""

import quality

# share of the learned answers that is worth asking about (lower than the incident rates:
# this is a question to the owner, not an alarm)
ASK_AT = {"refusal": 0.10, "error": 0.05, "empty": 0.05, "low_relevance": 0.10, "language": 0.10,
          "garbled": 0.05, "repetition": 0.05, "too_long": 0.10, "too_short": 0.10}
SPOT_CHECKS = 5


def build(rows, spot_results=()):
    """rows: [{"request_id", "quality_flags"}]. spot_results: [(request_id, spot_check dict)].
    Returns the review body."""
    n = len(rows)
    issues = []
    for cat, share in ASK_AT.items():
        hits = [r for r in rows if cat in (r.get("quality_flags") or [])]
        if n and len(hits) / n >= share:
            pct = round(100 * len(hits) / n)
            issues.append({"category": cat, "count": len(hits), "share": round(len(hits) / n, 3),
                           "examples": [r["request_id"] for r in hits[:3]],
                           "text": f"{pct}% of the answers it learned from are {quality.LABEL[cat]} ({len(hits)} of {n}). Intended?"})
    issues.sort(key=lambda i: -i["share"])
    checked = [(rid, s) for rid, s in spot_results if s]
    problems = [(rid, s) for rid, s in checked if s.get("likely_problem")]
    if problems:
        issues.append({"category": "spot_check", "count": len(problems), "share": round(len(problems) / len(checked), 3),
                       "examples": [rid for rid, _ in problems[:3]],
                       "text": f"Spot-checks: {len(problems)} of {len(checked)} sampled answers likely have a problem "
                               f"({problems[0][1].get('reason', '')[:120]})."})
    return {"answers": n, "issues": issues, "spot_checks": {"checked": len(checked), "likely_problems": len(problems)},
            "verdict": "check these" if issues else "looks fine"}


def slack_text(company_name, body):
    if not body["issues"]:
        return (f"*Baseline review: {company_name}*\nIt learned what normal looks like from {body['answers']} answers. "
                f"Nothing unusual in them ({body['spot_checks']['checked']} spot-checked).")
    lines = [f"*Baseline review: {company_name}*", f"It learned what normal looks like from {body['answers']} answers. "
             "Before that becomes the reference, check these:"]
    lines += [f"• {i['text']} e.g. {', '.join(f'`{x}`' for x in i['examples'])}" for i in body["issues"][:5]]
    lines.append("_If this is how the bot should answer, nothing to do. If not, fix it: ShiftLens compares everything "
                 "after this with these answers._")
    return "\n".join(lines)


RETRY_WAIT_S = 20


def run(company_id, company_name, anchor_ids, notify, chat=None, attempts=3):
    """Background: review the frozen baseline, store it, send it. Never raises.
    A dropped pooler connection is retried (it is the only chance this review gets)."""
    import time
    import psycopg2
    for attempt in range(1, attempts + 1):
        try:
            return _run(company_id, company_name, anchor_ids, notify, chat)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            if attempt == attempts:
                print(f"baseline review failed for company {company_id}: {type(e).__name__}", flush=True)
                return None
            time.sleep(RETRY_WAIT_S)
        except Exception as e:
            print(f"baseline review failed for company {company_id}: {type(e).__name__}", flush=True)
            return None


def _run(company_id, company_name, anchor_ids, notify, chat=None):
    import database
    import spotcheck
    with database.connection() as conn:
        company_name = company_name or database.company_name_by_id(conn, company_id)
        rows = database.rows_for_review(conn, anchor_ids)
        p = database.llm_usage_today(conn, model=spotcheck.PRIMARY, kind="spot_check")
        f = database.llm_usage_today(conn, model=spotcheck.FALLBACK, kind="spot_check")
        b = database.llm_usage_today(conn, kind="spot_check", company_id=company_id)
    # a few spot-checks, from the same daily budget; reuse any already done on these answers
    done = [(r["request_id"], r["spot_check"]) for r in rows if r.get("spot_check")]
    todo = [r for r in rows if not r.get("spot_check") and len(r["response"] or "") >= 20][: max(0, SPOT_CHECKS - len(done))]
    results, calls, new = list(done), [], []
    for r in todo:
        models = spotcheck.allowed_models(p["tokens"], p["units"], f["units"], b["units"] + len(calls))
        if not models:
            break
        res, used, _ = spotcheck.judge(r["prompt"], r["response"], None, chat or spotcheck.groq_json, models=models)
        calls += used
        if res:
            results.append((r["request_id"], res))
            new.append((r["id"], res))
    body = build(rows, results)
    with database.connection() as conn:
        for model in {m for m, _ in calls}:
            database.record_llm_usage(conn, model, "spot_check", company_id, units=sum(1 for m, _ in calls if m == model),
                                      requests=sum(1 for m, _ in calls if m == model), tokens=sum(t for m, t in calls if m == model))
        for output_id, res in new:
            database.save_spot_check(conn, output_id, res)
        database.save_baseline_review(conn, company_id, body)
    notify(company_id, slack_text(company_name, body))
    print(f"baseline review company {company_id}: {body['verdict']}, {len(body['issues'])} issue(s), "
          f"{body['spot_checks']['checked']} spot-checked", flush=True)
    return body
