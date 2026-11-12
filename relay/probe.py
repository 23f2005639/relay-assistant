"""Learn how the real service behaves, spending as few tokens as possible.

  python -m relay.probe latency --n 20     # spaced small calls: latency spread, headers, usage   (~4K tokens)
  python -m relay.probe cache              # same long opening twice: are cached tokens reported?  (~5K tokens)
  python -m relay.probe limit              # push until the per-minute limit answers 429            (~8K tokens)
  python -m relay.probe fit                # turn probe results into stand-in settings (no calls)

Everything is appended to runs/probes.jsonl with full headers and error bodies, and to results.csv
as scenario "probe-<mode>", source "real". Works against the stand-in too (--standin) for a dry run.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import math
import statistics
import time

from .config import ROOT, RUNS_DIR, Provider
from .llm import CallStats, CallTimeout, RateLimited, ServerError, StreamCut, TooLarge, stream_chat
from .loadgen import append_row

LOG = RUNS_DIR / "probes.jsonl"
FILLER = ("The Transformer uses stacked self-attention and point-wise, fully connected layers for both the "
          "encoder and decoder. ") * 60    # about 1,300 tokens of stable opening text for the cache probe


async def one(provider: Provider, mode: str, i: int, messages: list[dict], max_tokens: int = 40) -> dict:
    stats = CallStats()
    wall = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
    text, err, body = "", "", ""
    try:
        async for piece in stream_chat(provider, messages, max_tokens, stats):
            text += piece
    except RateLimited as e:
        err, body = f"429 {e.kind} retry_after={e.retry_after}", e.body
    except (TooLarge, ServerError, CallTimeout, StreamCut, RuntimeError) as e:
        err, body = type(e).__name__, str(e)[:500]
    ms = lambda t: None if t is None else round((t - stats.t_send) * 1000, 1)
    rec = {"mode": mode, "i": i, "wall": wall, "http": stats.status, "error": err, "error_body": body,
           "first_byte_ms": ms(stats.t_first_byte), "first_token_ms": ms(stats.t_first_token), "total_ms": ms(stats.t_end),
           "tokens_in": stats.tokens_in, "tokens_out": stats.tokens_out, "cached_tokens": stats.cached_tokens,
           "headers": stats.headers, "model": provider.model, "source": provider.source, "reply": text[:120]}
    RUNS_DIR.mkdir(exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    status = "ok" if not err else "rate_limited" if err.startswith("429") or err == "TooLarge" else "timeout" if err == "CallTimeout" else "error"
    append_row({"request_id": f"probe-{mode}-{int(time.time())}-{i:03d}", "scenario": f"probe-{mode}", "source": provider.source,
                    "start_time": wall, "first_token_ms": rec["first_token_ms"] or "", "total_ms": rec["total_ms"] or "",
                    "status": status, "retries": 0, "tokens_in": stats.tokens_in, "tokens_out": stats.tokens_out,
                    "retrieval_setting": "none", "answer_grade": "", "level": "probe", "reason": err})
    h = stats.headers
    print(f"{mode}[{i}] http={stats.status} ttft={rec['first_token_ms']}ms total={rec['total_ms']}ms in={stats.tokens_in} "
          f"out={stats.tokens_out} cached={stats.cached_tokens} rem_tok={h.get('x-ratelimit-remaining-tokens')} "
          f"rem_req={h.get('x-ratelimit-remaining-requests')} {err}")
    if body:
        print("   body:", body[:300])
    return rec


async def latency(p: Provider, n: int, gap: float):
    for i in range(n):
        await one(p, "latency", i, [{"role": "user", "content": f"Reply with the single word: ok{i}"}], 20)
        await asyncio.sleep(gap)


async def cache(p: Provider):
    for i in range(3):
        await one(p, "cache", i, [{"role": "system", "content": FILLER},
                                  {"role": "user", "content": f"In five words, what is described? ({i})"}], 30)
        await asyncio.sleep(3)


async def limit(p: Provider, size_words: int, max_calls: int):
    """Send same-size calls back to back until the service refuses, then one more after its retry-after."""
    pad = "alpha " * size_words
    for i in range(max_calls):
        rec = await one(p, "limit", i, [{"role": "user", "content": pad + "\nReply with: ok"}], 10)
        if rec["http"] in (429, 413):
            ra = rec["headers"].get("retry-after")
            print(f"   refused; retry-after={ra}. Waiting that long, then one more call to time the recovery.")
            await asyncio.sleep(float(ra or 5) + 0.5)
            await one(p, "limit-recovery", i + 1, [{"role": "user", "content": "Reply with: ok"}], 10)
            return
    print("   never refused within max_calls")


async def daily(p: Provider, size_words: int, max_calls: int):
    """Spend what is left of today in equal calls until the provider refuses with a DAILY limit. Then ask:
    is a much smaller call still accepted, and does a same-size call pass again after the wait it names?"""
    pad = "alpha " * size_words
    big = [{"role": "user", "content": pad + "\nReply with: ok"}]
    tiny = [{"role": "user", "content": "Reply with: ok"}]
    for i in range(max_calls):
        rec = await one(p, "daily", i, big, 10)
        ra = float(rec["headers"].get("retry-after") or 5)
        if rec["http"] == 429 and "per_day" in rec["error"]:
            print(f"   DAILY limit refused call {i + 1}; retry-after={ra:.0f}s")
            await one(p, "daily-small-right-after", i, tiny, 8)
            if ra <= 1200:
                await asyncio.sleep(ra + 1)
                await one(p, "daily-recovery-same-size", i, big, 10)
            return
        # a per-minute refusal is not what we are looking for: wait it out. Otherwise pace to the minute limit.
        await asyncio.sleep(ra + 0.5 if rec["http"] == 429 else size_words * 1.05 / (8000 / 60))
    print("   the daily limit was not reached within max_calls")


async def concurrent(p: Provider, n: int):
    """n tiny calls at the same instant: is there a cap on calls in flight, and do they slow each other down?"""
    solo = await one(p, "concurrent-solo", 0, [{"role": "user", "content": "Reply with the single word: ok"}], 20)
    await asyncio.sleep(2)
    recs = await asyncio.gather(*[one(p, f"concurrent-{n}", i, [{"role": "user", "content": f"Reply with the single word: ok{i}"}], 20)
                                  for i in range(n)])
    ok = [r["total_ms"] for r in recs if not r["error"]]
    print(f"   solo {solo['total_ms']} ms; {n} at once: {len(ok)} ok, median {statistics.median(ok) if ok else None} ms, "
          f"max {max(ok) if ok else None} ms, refused {n - len(ok)}")


async def rpm(p: Provider, n: int, spacing: float = 0.3):
    """n tiny calls, one every `spacing` seconds without waiting for replies. The call number at which the
    first refusal arrives tells a fixed window (refuses at limit+1) from a refilling bucket (refuses later)."""
    t0 = time.monotonic()

    async def fire(i):
        await asyncio.sleep(i * spacing)
        rec = await one(p, "rpm", i, [{"role": "user", "content": "Reply with: ok"}], 8)
        rec["sent_at"] = round(i * spacing, 1)
        return rec
    recs = await asyncio.gather(*[fire(i) for i in range(n)])
    bad = [r for r in recs if r["http"] == 429]
    print(f"   {n} calls in {time.monotonic() - t0:.1f}s: {n - len(bad)} accepted, {len(bad)} refused")
    if bad:
        first = min(bad, key=lambda r: r["i"])
        print(f"   first refusal: call {first['i'] + 1}, sent at t={first['sent_at']}s, retry-after={first['headers'].get('retry-after')}")
        await asyncio.sleep(float(first["headers"].get("retry-after") or 2) + 0.5)
        await one(p, "rpm-recovery", n, [{"role": "user", "content": "Reply with: ok"}], 8)


def fit():
    """Lognormal fit of real time-to-first-token and token speed -> stand-in settings."""
    recs = [json.loads(l) for l in LOG.open()] if LOG.exists() else []
    ok = [r for r in recs if r["source"] == "real" and r["mode"] == "latency" and not r["error"] and r["first_token_ms"]]
    if len(ok) < 5:
        raise SystemExit(f"need at least 5 successful real latency probes, have {len(ok)}")
    logs = [math.log(r["first_token_ms"] / 1000) for r in ok]
    rates = [r["tokens_out"] / max(1e-3, (r["total_ms"] - r["first_token_ms"]) / 1000) for r in ok if r["tokens_out"] > 3]
    cfg = {"ttft_median_s": round(math.exp(statistics.median(logs)), 3), "ttft_sigma": round(statistics.pstdev(logs), 3),
           "tokens_per_s": round(statistics.median(rates), 1) if rates else 400.0}
    print(f"tiny prompts ({len(ok)} calls): first token median {cfg['ttft_median_s']}s")
    # Better: full-size pipeline calls, if results.csv has any (quality runs and real load runs).
    # First token of the model call = first shown token minus time spent queued for budget.
    rows = [r for r in csv.DictReader((ROOT / "results.csv").open())
            if r["source"] == "real" and r["level"] in ("full", "lean") and r["status"] == "ok" and r["retries"] == "0"
            and r["first_token_ms"] and int(r["tokens_in"] or 0) > 0 and r.get("coalesced") != "1"]
    if len(rows) >= 10:
        idle = lambda r: float(r["wait_ms"] or 0) + float(r["retry_ms"] or 0)
        pts = [(int(r["tokens_in"]) / 1000, (float(r["first_token_ms"]) - idle(r)) / 1000) for r in rows]
        pts += [(r["tokens_in"] / 1000, r["first_token_ms"] / 1000) for r in ok]     # the tiny probe calls anchor the base
        pts = [(x, y) for x, y in pts if y > 0.05]
        # first token = base + per_1k * prompt tokens (least squares), times lognormal noise
        n = len(pts)
        mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
        slope = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
        slope = max(0.0, slope)
        base = max(0.05, my - slope * mx)
        resid = [math.log(y / (base + slope * x)) for x, y in pts]
        stream = [(float(r["total_ms"]) - float(r["first_token_ms"])) / 1000 for r in rows]
        outs = [int(r["tokens_out"]) for r in rows]
        ins = [int(r["tokens_in"]) for r in rows]
        cfg = {"ttft_median_s": round(base, 3), "ttft_per_1k_in_s": round(slope, 3), "ttft_sigma": round(statistics.pstdev(resid), 3),
               # the real reply arrives almost in one piece after the first token: speed = tokens / (total - first)
               "tokens_per_s": round(statistics.median(outs) / max(0.005, statistics.median(stream)), 0),
               "extra_out_tokens": max(0, int(statistics.median(outs)) - 45)}   # 45 = the stand-in's own reply length, measured 2026-11-11; re-measure if its reply changes
        ys = sorted(y for _, y in pts)
        print(f"all sizes ({n} calls, up to {max(ins)} prompt tokens): first token min {ys[0]:.2f}s "
              f"median {statistics.median(ys):.2f}s max {ys[-1]:.2f}s; fitted {base:.2f}s + {slope:.2f}s per 1,000 prompt tokens; "
              f"reply after first token median {statistics.median(stream) * 1000:.0f} ms; output tokens median {statistics.median(outs):.0f}")
    # Do calls in flight slow each other down? Compare the solo call with the simultaneous ones.
    solo = [r["total_ms"] for r in recs if r["mode"] == "concurrent-solo" and not r["error"]]
    many = [r for r in recs if r["mode"].startswith("concurrent-") and r["mode"] != "concurrent-solo" and not r["error"]]
    if solo and many:
        k = int(many[0]["mode"].split("-")[1])
        ratio = statistics.median(r["total_ms"] for r in many) / statistics.median(solo)
        cfg["load_slowdown"] = round(max(0.0, (ratio - 1) / (k - 1)), 3)
        print(f"{k} calls at once took {ratio:.2f}x the solo time -> slowdown per extra call in flight {cfg['load_slowdown']}")
    # Typed in, not recomputed: median over 43 quality-run calls on 2026-11-11 (range 2.86-4.40). Re-measure if the prompt changes.
    cfg["chars_per_token"] = 3.73
    (RUNS_DIR / "standin_calibrated.json").write_text(json.dumps(cfg, indent=1))
    print("stand-in settings:", cfg, "-> runs/standin_calibrated.json (loadgen reads this file automatically)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["latency", "cache", "limit", "concurrent", "rpm", "daily", "fit"])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--gap", type=float, default=3.0, help="seconds between latency probes")
    ap.add_argument("--size-words", type=int, default=1200, help="limit probe: words per call (about 1 token each)")
    ap.add_argument("--max-calls", type=int, default=10)
    ap.add_argument("--standin", action="store_true", help="dry run against a stand-in already running on :8089")
    ap.add_argument("--model")
    a = ap.parse_args()
    if a.mode == "fit":
        fit()
    else:
        p = Provider.standin() if a.standin else Provider()
        if a.model:
            p.model = a.model
        if not p.api_key:
            raise SystemExit("GROQ_API_KEY is not set")
        asyncio.run({"latency": lambda: latency(p, a.n, a.gap), "cache": lambda: cache(p),
                     "limit": lambda: limit(p, a.size_words, a.max_calls),
                     "concurrent": lambda: concurrent(p, min(a.n, 12)), "rpm": lambda: rpm(p, a.n),
                     "daily": lambda: daily(p, a.size_words, a.max_calls)}[a.mode]())
