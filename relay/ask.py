"""Ask one question and watch the checked reply stream.

  python -m relay.ask "What BLEU does the big Transformer get on EN-DE?"
  python -m relay.ask --standin "..."        # starts a stand-in in-process (no key needed)
  python -m relay.ask --eval small           # run questions.json through one retrieval setting -> results.csv
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

from .config import DEFAULT_SETTING, RETRIEVAL_SETTINGS, RUNS_DIR, Provider
from .control import Controller
from .loadgen import append_row, load_questions
from .pipeline import Pipeline
from .standin import calibrated, start


async def main(a):
    runner = None
    if a.standin:
        _, runner = await start(calibrated(), 8089)
        provider = Provider.standin()
    else:
        provider = Provider()
        if not provider.api_key:
            raise SystemExit("GROQ_API_KEY is not set (or use --standin)")
    pipe = Pipeline(provider, Controller(day_file=None if a.standin else RUNS_DIR / "day_ledger.txt"), a.eval or a.setting)
    pipe.policy.coalesce = False
    pipe.policy.force_full = bool(a.eval)
    if a.eval:
        RUNS_DIR.mkdir(exist_ok=True)
        answers = (RUNS_DIR / f"answers-{a.eval}-{provider.source}{'-heldout' if a.questions else ''}.jsonl").open("a")
        if a.questions:        # a separate file, e.g. questions written after the system was frozen
            todo = json.loads(open(a.questions).read())
            scenario = f"retrieval-{a.eval}-heldout"
        else:
            todo = [q for q in load_questions() if (q.get("core") or not a.core) and not (a.rest and q.get("core"))]
            scenario = f"retrieval-{a.eval}" + ("-extra" if a.rest else "")
        for i, q in enumerate(todo):
            if i and a.gap:
                await asyncio.sleep(a.gap)      # let the token bucket refill, so latency is not queueing
            wall = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
            r = await pipe.answer(q["question"], patience_s=90)   # quality run: wait for budget rather than degrade
            print(f"[{q['id']}] {r.level}/{r.status} {r.total_ms:.0f}ms in={r.tokens_in} out={r.tokens_out}\n{r.text}\n")
            answers.write(json.dumps({"id": q["id"], "question": q["question"], "gold": q.get("gold"), "level": r.level,
                                      "text": r.text, "citations": r.citations, "reason": r.reason,
                                      "dropped_sentences": r.dropped_sentences, "bad_quotes": r.bad_quotes, "raw": r.raw,
                                      "evidence": round(r.evidence, 2), "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                                      "reasoning_tokens": r.reasoning_tokens, "total_ms": round(r.total_ms),
                                      "ledger": pipe.ctl.trace[-1] if pipe.ctl.trace else None}) + "\n")
            answers.flush()
            append_row({"request_id": f"{scenario}-{q['id']}", "scenario": scenario, "source": provider.source,
                         "start_time": wall, "first_token_ms": "" if r.first_token_ms is None else round(r.first_token_ms, 1),
                         "total_ms": round(r.total_ms, 1), "status": r.status, "retries": r.retries, "tokens_in": r.tokens_in,
                         "tokens_out": r.tokens_out, "retrieval_setting": pipe.setting.name,
                         "answer_grade": "",   # YOU fill this in by hand: correct / wrong / refused_correctly / refused_wrongly
                         "level": r.level, "wait_ms": round(r.wait_ms, 1), "work_ms": round(r.work_ms, 1),
                         "retry_ms": round(r.retry_ms, 1), "question_id": q["id"], "reason": r.reason})
    else:
        r = await pipe.answer(a.question, emit=lambda s: (sys.stdout.write(s), sys.stdout.flush()))
        print(f"\n--- level={r.level} status={r.status} evidence={r.evidence:.2f} first_verified={r.first_token_ms and round(r.first_token_ms)}ms "
              f"raw_first={r.raw_first_token_ms and round(r.raw_first_token_ms)}ms total={r.total_ms:.0f}ms "
              f"tokens={r.tokens_in}+{r.tokens_out} (thinking {r.reasoning_tokens}) cached={r.cached_tokens} retries={r.retries} dropped={r.dropped_sentences} "
              f"bad_quotes={r.bad_quotes}\n--- {r.reason}")
        for kind, name, s, e in r.spans:
            print(f"    {kind:5s} {e - s:8.1f} ms  {name}")
    if runner:
        await runner.cleanup()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--standin", action="store_true")
    ap.add_argument("--setting", default=DEFAULT_SETTING, choices=list(RETRIEVAL_SETTINGS))
    ap.add_argument("--eval", choices=list(RETRIEVAL_SETTINGS))
    ap.add_argument("--questions", help="with --eval: run this question file instead of questions.json")
    ap.add_argument("--rest", action="store_true", help="with --eval: only the questions NOT marked core")
    ap.add_argument("--gap", type=float, default=0.0, help="with --eval: seconds to pause between questions")
    ap.add_argument("--core", action="store_true", help="with --eval: only the 15 questions marked core (saves daily budget)")
    a = ap.parse_args()
    if not a.question and not a.eval:
        ap.error("give a question or --eval")
    asyncio.run(main(a))
