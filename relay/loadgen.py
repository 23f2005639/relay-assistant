"""Open-loop load generator. Writes one row per request to results.csv.

Open loop means arrivals follow a fixed schedule and never wait for earlier replies, the way real
people behave. Every latency is measured from the SCHEDULED arrival time, so a stalled system cannot
hide its stall by delaying the next request (the "coordinated omission" mistake).

  python -m relay.loadgen steady            # stand-in, started in-process
  python -m relay.loadgen burst --dup 0.5
  python -m relay.loadgen steady --real --minutes 2 --rate 2   # real Groq; spends your daily budget
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import random
import time
import zlib
from pathlib import Path

import httpx

from .config import DEFAULT_SETTING, ROOT, RUNS_DIR, Limits, Policy, Provider
from .control import Controller
from .pipeline import Pipeline, Result
from .standin import calibrated, start

COLUMNS = ["request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms", "status", "retries",
           "tokens_in", "tokens_out", "retrieval_setting", "answer_grade",
           # extra columns, after the required ones
           "level", "wait_ms", "work_ms", "retry_ms", "raw_first_token_ms", "queue_len", "coalesced",
           "question_id", "reason"]

# Each scenario: how long, how many arrivals per minute over time, and what the stand-in does.
# Free-tier capacity is roughly 3 to 4 answers per minute, so these runs are small on purpose.
SCENARIOS = {
    "steady": dict(minutes=10, rate=lambda t: 2.5, note="below capacity"),
    "burst": dict(minutes=6, rate=lambda t: 1.0, burst=(60, 20, 40), note="40 extra arrivals in 20 s at t=60 s"),
    "overload": dict(minutes=8, rate=lambda t: 8.0, note="about twice capacity, sustained"),
    "halved": dict(minutes=8, rate=lambda t: 2.5, events=[(180, {"tpm": 4000})], note="per-minute limit halved at t=180 s, unannounced"),
    # Not a load shape: a check of the measuring harness itself. The stand-in stalls for 15 s with limits out
    # of the way; an honest harness must report about rate x 15 s slow requests, the slowest near 15 s.
    "freeze": dict(minutes=2, rate=lambda t: 12.0, events=[(30, {"freeze_s": 15})],
                   standin=dict(tpm=10_000_000, rpm=10_000), note="known 15 s stall at t=30 s"),
    "daily": dict(minutes=10, rate=lambda t: 3.0, standin=dict(tpd=30_000, day_seconds=3600.0),
                  note="daily budget (scaled to 30K tokens) runs out mid-run"),
}

SMOKE_QUESTIONS = [  # placeholders so the harness runs; replace with questions.json that YOU write
    {"id": "s1", "question": "What BLEU score does the big Transformer reach on English-to-German?"},
    {"id": "s2", "question": "What is the theoretical latency of Moshi?"},
    {"id": "s3", "question": "How does H-Net decide where chunk boundaries go?"},
    {"id": "s4", "question": "What frame rate does the Mimi codec operate at?"},
    {"id": "s5", "question": "Which optimizer was used to train the original Transformer?"},
    {"id": "s6", "question": "What is the ratio loss in dynamic chunking?"},
    {"id": "s7", "question": "What is the best pizza topping?"},
]


def append_row(row: dict, out: Path = ROOT / "results.csv"):
    """One row, written at once, so a run that is stopped keeps everything that had finished."""
    new = not out.exists()
    with out.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def load_questions() -> list[dict]:
    p = ROOT / "questions.json"
    return json.loads(p.read_text()) if p.exists() else SMOKE_QUESTIONS


def schedule(spec: dict, minutes: float, rate_override: float | None, seed: int) -> list[float]:
    """Poisson arrivals (seconds from start), plus an optional burst."""
    rng = random.Random(seed)
    t, out, end = 0.0, [], minutes * 60
    while True:
        r = rate_override if rate_override else spec["rate"](t)
        t += rng.expovariate(r / 60.0)
        if t >= end:
            break
        out.append(t)
    if "burst" in spec and not rate_override:
        at, width, n = spec["burst"]
        out += [at + rng.uniform(0, width) for _ in range(n)]
    return sorted(out)


async def one_request(i: int, at: float, t_start: float, wall_start: dt.datetime, pipe: Pipeline, q: dict,
                      scenario: str, policy: Policy, rng: random.Random) -> dict:
    arrived = t_start + at
    await asyncio.sleep(max(0.0, arrived - time.monotonic()))
    patience = policy.patience_s * rng.uniform(0.7, 1.3)       # people differ
    first = asyncio.Event()
    # A "hot" arrival repeats a question word for word and may share its answer. Every other arrival
    # stands for a different question, so it gets its own key and can never share.
    task = asyncio.create_task(pipe.answer(q["question"], emit=lambda s: first.set(), patience_s=patience, arrived=arrived,
                                           share_key=None if q.get("_hot") else f"{q['question']}#{i}"))
    waiter = asyncio.create_task(first.wait())
    await asyncio.wait({task, waiter}, timeout=max(0.0, arrived + patience - time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
    res: Result | None = None
    if not first.is_set() and not task.done():
        task.cancel()                                           # the person gave up: nothing on screen in time
        status, reason = "timeout", f"gave up after {patience:.1f}s with nothing shown"
    else:
        try:
            res = await asyncio.wait_for(task, timeout=max(0.1, arrived + policy.hard_cap_s - time.monotonic()))
            status, reason = res.status, res.reason
        except asyncio.TimeoutError:
            status, reason = "timeout", "reply started but did not finish before the hard cap"
        except Exception as e:                                  # a bug or an unhandled provider error
            status, reason = "error", f"{type(e).__name__}: {e}"[:200]
    waiter.cancel()
    now_ms = (time.monotonic() - arrived) * 1000
    f = lambda v: "" if v is None else round(v, 1)
    return {"request_id": f"{scenario}-{pipe.provider.source}-{i:04d}", "scenario": scenario, "source": pipe.provider.source,
            "start_time": (wall_start + dt.timedelta(seconds=at)).isoformat(timespec="milliseconds"),
            "first_token_ms": f(res.first_token_ms) if res else "", "total_ms": f(res.total_ms if res else now_ms),
            "status": status, "retries": res.retries if res else 0,
            "tokens_in": res.tokens_in if res else 0, "tokens_out": res.tokens_out if res else 0,
            "retrieval_setting": pipe.setting.name, "answer_grade": "",
            "level": res.level if res else "none", "wait_ms": f(res.wait_ms) if res else "",
            "work_ms": f(res.work_ms) if res else "", "retry_ms": f(res.retry_ms) if res else "",
            "raw_first_token_ms": f(res.raw_first_token_ms) if res else "", "queue_len": res.queue_len if res else "",
            "coalesced": int(res.coalesced) if res else 0, "question_id": q["id"], "reason": reason,
            "_spans": res.spans if res else [], "_text": res.text if res else ""}


async def run(scenario: str, real: bool, minutes: float | None, rate: float | None, dup: float, setting: str,
              seed: int | None, port: int, coalesce: bool, standin_overrides: dict, out: Path,
              label: str | None = None, policy_overrides: dict | None = None) -> list[dict]:
    spec = SCENARIOS[scenario]
    minutes = minutes or spec["minutes"]
    if seed is None:        # each scenario gets its own arrivals; the same scenario always gets the same ones
        seed = zlib.crc32(scenario.encode()) % 1000
    policy = Policy(coalesce=coalesce, **(policy_overrides or {}))
    runner = None
    if real:
        provider, limits = Provider(), Limits()
        if not provider.api_key:
            raise SystemExit("GROQ_API_KEY is not set")
    else:
        cfg = calibrated(**{**spec.get("standin", {}), **standin_overrides})
        _, runner = await start(cfg, port)
        provider = Provider.standin(port)
        # The controller starts from the documented limits and must discover anything different by itself.
        limits = Limits(day_seconds=cfg.day_seconds, tpd=cfg.tpd)
    pipe = Pipeline(provider, Controller(limits, policy, seed, RUNS_DIR / "day_ledger.txt" if real else None), setting, policy)
    questions = load_questions()
    rng = random.Random(seed)
    hot = questions[: max(1, len(questions) // 4)]              # "the incident everyone asks about"
    arrivals = schedule(spec, minutes, rate, seed)
    print(f"{scenario}: {len(arrivals)} arrivals over {minutes:g} min, source={provider.source}, dup={dup}, coalesce={coalesce}")
    t_start, wall_start = time.monotonic() + 0.5, dt.datetime.now().astimezone() + dt.timedelta(seconds=0.5)

    async def events():
        async with httpx.AsyncClient() as c:
            for at, change in spec.get("events", []):
                if real or at > minutes * 60:
                    continue
                await asyncio.sleep(max(0.0, t_start + at - time.monotonic()))
                await c.post(f"http://127.0.0.1:{port}/admin/config", json=change)
                print(f"  t={at}s stand-in changed: {change}")

    async def logged(*args) -> dict:
        row = await one_request(*args)
        append_row(row, out)
        return row

    ev = asyncio.create_task(events())
    rows = await asyncio.gather(*[
        logged(i, at, t_start, wall_start, pipe,
                    {**rng.choice(hot), "_hot": True} if rng.random() < dup else rng.choice(questions),
                    label or scenario, policy, rng) for i, at in enumerate(arrivals)])
    ev.cancel()
    if runner:
        await runner.cleanup()

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with (RUNS_DIR / f"{label or scenario}-{provider.source}-{stamp}.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps({k: v for k, v in r.items()}) + "\n")
        fh.write(json.dumps({"controller_events": [e[1] for e in pipe.ctl.events], "peak_queue": pipe.ctl.peak_queue,
                             "limits_learned": vars(pipe.ctl.limits), "ledger_trace": pipe.ctl.trace,
                             "policy": vars(policy)}) + "\n")
    for e in pipe.ctl.events:
        print("  controller:", e[1])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=list(SCENARIOS))
    ap.add_argument("--real", action="store_true", help="use Groq instead of the stand-in")
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--rate", type=float, help="constant arrivals per minute (overrides the scenario shape)")
    ap.add_argument("--dup", type=float, default=0.3, help="share of arrivals asking one of the 'hot' questions")
    ap.add_argument("--setting", default=DEFAULT_SETTING)
    ap.add_argument("--seed", type=int, help="default: derived from the scenario name")
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--no-coalesce", action="store_true")
    ap.add_argument("--standin", default="{}", help='JSON overrides for the stand-in, e.g. \'{"p_slow":0.1}\'')
    ap.add_argument("--out", default=str(ROOT / "results.csv"))
    ap.add_argument("--label", help="scenario name written to results.csv (default: the scenario)")
    ap.add_argument("--policy", default="{}", help='JSON overrides for Policy, e.g. \'{"max_attempts":1}\' or \'{"naive":true,"max_attempts":5}\'')
    a = ap.parse_args()
    rows = asyncio.run(run(a.scenario, a.real, a.minutes, a.rate, a.dup, a.setting, a.seed, a.port,
                           not a.no_coalesce, json.loads(a.standin), Path(a.out), a.label, json.loads(a.policy)))
    from .report import summarize
    print(summarize(rows))


if __name__ == "__main__":
    main()
