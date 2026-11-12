"""Recompute the results tables from results.csv, the same way a reviewer would.

  python -m relay.report            # prints Tables 2a, 2b and 3 as markdown

Percentiles are nearest-rank over ALL requests in the scenario. A request that never showed a first
token (the person gave up, or it failed) counts as slower than everything else, so it pushes the
slow end up instead of vanishing. When a percentile lands on such a request it prints as "none".
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

from .config import PRICE_IN_PER_M, PRICE_OUT_PER_M, ROOT


def pct(values: list[float | None], p: float) -> str:
    xs = sorted(math.inf if v is None else v for v in values)
    if not xs:
        return "not measured"
    v = xs[max(0, math.ceil(p / 100 * len(xs)) - 1)]
    return "none" if v == math.inf else f"{v:.0f}"


def num(s) -> float | None:
    return float(s) if s not in ("", None) else None


def summarize(rows: list[dict]) -> str:
    by = defaultdict(list)
    for r in rows:
        by[(r["scenario"], r["source"])].append(r)
    a = ["| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |",
         "|---|---|---:|---:|---:|---|---|"]
    b = ["| Scenario | Succeeded (%) | Rate-limited (%) | Timed out or given up (%) | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for (sc, src), rs in by.items():
        n = len(rs)
        from datetime import datetime
        ts = sorted(datetime.fromisoformat(r["start_time"]) for r in rs)
        dur = max(1.0, (ts[-1] - ts[0]).total_seconds())
        ft = [num(r["first_token_ms"]) for r in rs]
        tt = [num(r["total_ms"]) if r["status"] != "timeout" else None for r in rs]
        a.append(f"| {sc} | {src} | {n} | {dur:.0f} | {n / dur * 60:.1f} | "
                 f"{pct(ft, 50)} / {pct(ft, 95)} / {pct(ft, 99)} | {pct(tt, 50)} / {pct(tt, 95)} / {pct(tt, 99)} |")
        share = lambda s: 100 * sum(r["status"] == s for r in rs) / n
        answers = [r for r in rs if int(r["tokens_in"] or 0) > 0]
        tok_in = sum(int(r["tokens_in"]) for r in answers)
        tok_out = sum(int(r["tokens_out"]) for r in answers)
        tpa = (tok_in + tok_out) / len(answers) if answers else 0
        cost = 1000 * (tok_in * PRICE_IN_PER_M + tok_out * PRICE_OUT_PER_M) / 1e6 / len(answers) if answers else 0
        q = [int(r["queue_len"]) for r in rs if r.get("queue_len") not in ("", None)]
        b.append(f"| {sc} ({src}) | {share('ok'):.1f} | {share('rate_limited'):.1f} | {share('timeout'):.1f} | "
                 f"{sum(int(r['retries']) for r in rs) / n:.2f} | {max(q) if q else 0} | {tpa:.0f} | ${cost:.2f} |")
    # Passages need no model call and appear in milliseconds, so they pull the median first-token time down.
    # This extra table separates what people received.
    extra = ["", "What people received, and how long the first token took for each kind (not a required table):",
             "| Scenario | Model answer: n, first token p50 / p95 (ms) | Shared answer: n | Quoted passages: n, p50 (ms) | Refused: n | Nothing shown: n |",
             "|---|---|---:|---|---:|---:|"]
    for (sc, src), rs in by.items():
        model = [r for r in rs if r.get("level") in ("full", "lean") and r.get("coalesced") != "1"]
        shared = [r for r in rs if r.get("coalesced") == "1"]
        passages = [r for r in rs if r.get("level") == "extractive" and r.get("coalesced") != "1"]
        refused = [r for r in rs if r.get("level") == "refused" and r.get("coalesced") != "1"]
        nothing = [r for r in rs if r.get("level") in ("none", "deferred")]
        mf = [num(r["first_token_ms"]) for r in model]
        pf = [num(r["first_token_ms"]) for r in passages]
        extra.append(f"| {sc} ({src}) | {len(model)}, {pct(mf, 50)} / {pct(mf, 95)} | {len(shared)} | "
                     f"{len(passages)}, {pct(pf, 50)} | {len(refused)} | {len(nothing)} |")
    return "\n".join(["### Table 2a", *a, "", "### Table 2b", *b, *extra])


QUALITY = {"retrieval-small", "retrieval-medium", "retrieval-large"}   # the same 15 questions under each setting


def _quality_row(name: str, rs: list[dict]) -> str:
    n = len(rs)
    graded = [r for r in rs if r["answer_grade"]]
    g = lambda grade: f"{100 * sum(r['answer_grade'] == grade for r in graded) / len(graded):.1f}" if graded else "not measured"
    # Quality runs are sent back to back and always wait for a full answer, so they queue for token
    # budget. That queueing says nothing about the retrieval setting, so it is subtracted here.
    idle = lambda r: (num(r.get("wait_ms")) or 0) + (num(r.get("retry_ms")) or 0)
    ft = [None if num(r["first_token_ms"]) is None else num(r["first_token_ms"]) - idle(r) for r in rs]
    tt = [num(r["total_ms"]) - idle(r) for r in rs]
    tpa = sum(int(r["tokens_in"]) + int(r["tokens_out"]) for r in rs) / n
    return (f"| {name} | {n} | {g('correct')} | {g('wrong')} | {g('refused_correctly')} | {tpa:.0f} | "
            f"{pct(ft, 50)} / {pct(ft, 95)} | {pct(tt, 50)} / {pct(tt, 95)} |")


def table3(rows: list[dict]) -> str:
    by = defaultdict(list)
    for r in rows:
        if r["scenario"] in QUALITY and r["source"] == "real":
            by[r["retrieval_setting"]].append(r)
    out = ["### Table 3",
           "| Setting | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |",
           "|---|---:|---:|---:|---:|---:|---|---|"]
    out += [_quality_row(setting, rs) for setting, rs in by.items()]
    out += ["", "Times exclude time queued for token budget and time spent retrying (first_token_ms - wait_ms - retry_ms). "
            "The share not shown in a row was refused although the papers do answer (grade refused_wrongly)."]
    full = [r for r in rows if r["scenario"] in ("retrieval-medium", "retrieval-medium-extra") and r["source"] == "real"]
    if len(full) > 15:
        out += ["", "Chosen setting on every question written (not a required table):",
                "| Setting | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |",
                "|---|---:|---:|---:|---:|---:|---|---|", _quality_row(full[0]["retrieval_setting"], full)]
    return "\n".join(out)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results.csv"
    rows = list(csv.DictReader(path.open()))
    print(summarize([r for r in rows if not r["scenario"].startswith(("retrieval", "probe"))]) if any(
        not r["scenario"].startswith(("retrieval", "probe")) for r in rows) else "no load rows yet")
    print()
    print(table3(rows))
