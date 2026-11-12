"""Where did the time go for one request?

  python -m relay.trace runs/overload-stand-in-....jsonl            # the slowest answered request in the run
  python -m relay.trace runs/overload-stand-in-....jsonl overload-0012

Reads the per-request spans the pipeline records (wait = queued for token budget, work = local steps
and the first model call, retry = backoff sleeps and repeated calls) and says how much of each fell
before the first token reached the person.
"""
from __future__ import annotations

import json
import sys


def explain(row: dict) -> str:
    ft = row["first_token_ms"] if row["first_token_ms"] != "" else None
    lines = [f"{row['request_id']}  status={row['status']}  level={row['level']}  total={row['total_ms']} ms  "
             f"first token={ft} ms  retries={row['retries']}  queue length on arrival={row['queue_len']}",
             f"  reason: {row['reason'] or '-'}"]
    before = {"wait": 0.0, "work": 0.0, "retry": 0.0}
    after = dict(before)
    for kind, name, start, end in row["_spans"]:
        cut = min(end, ft) if ft is not None else end
        b = max(0.0, cut - start)
        before[kind] += b
        after[kind] += (end - start) - b
        lines.append(f"  {start:9.1f} -> {end:9.1f} ms  {kind:5s}  {name}")
    tot = lambda d: sum(d.values())
    lines.append(f"  before first token: waiting {before['wait']:.0f} ms, working {before['work']:.0f} ms, retrying {before['retry']:.0f} ms"
                 f"  (= {tot(before):.0f} ms)")
    lines.append(f"  after first token:  waiting {after['wait']:.0f} ms, working {after['work']:.0f} ms, retrying {after['retry']:.0f} ms"
                 f"  (= {tot(after):.0f} ms)")
    return "\n".join(lines)


def main():
    rows = [json.loads(l) for l in open(sys.argv[1])]
    rows = [r for r in rows if "request_id" in r]
    if len(sys.argv) > 2:
        pick = next(r for r in rows if r["request_id"] == sys.argv[2])
    else:
        answered = [r for r in rows if r["_spans"] and r["first_token_ms"] != ""]
        pick = max(answered, key=lambda r: r["total_ms"])
    print(explain(pick))


if __name__ == "__main__":
    main()
