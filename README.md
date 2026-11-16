# The Relay

A five-step assistant that answers questions from three arXiv papers and stays truthful when the
model provider's limits are hit.

    route -> retrieve (tool) -> decide (branch + one loop) -> draft (one model call) -> check

Only the draft step spends tokens. Under pressure a reply gets less polished, never less truthful:
full answer -> lean answer -> passages quoted word for word -> honest refusal.

## Setup

    uv venv .venv && uv pip install --python .venv/bin/python httpx aiohttp pytest pytest-asyncio
    echo 'GROQ_API_KEY=...' > .env      # read by relay/config.py; git-ignored; never logged
    .venv/bin/python -m pytest tests -q

The PDFs are in `papers/library/` at the exact versions the brief names. `pdftotext` (poppler) must be
installed. `fastembed` and `numpy` are needed only for the embedding experiment in `relay/recall.py`.

## Run

| What | Command | Spends real tokens |
|---|---|---|
| One question, stand-in | `python -m relay.ask --standin "..."` | no |
| One question, Groq | `python -m relay.ask "..."` | about 2K |
| Learn the real service | `python -m relay.probe latency`, `cache`, `limit`, `concurrent`, `rpm` | about 25K in total |
| Find the daily limit | `python -m relay.probe daily --size-words 3000` | everything left today |
| Calibrate the stand-in | `python -m relay.probe fit` (writes `runs/standin_calibrated.json`) | no |
| Load test, stand-in | `python -m relay.loadgen steady` (also `burst`, `overload`, `halved`, `daily`, `freeze`) | no |
| Load test, Groq | `python -m relay.loadgen burst --real --minutes 2` | up to 8K a minute |
| Retrieval check, free | `python -m relay.recall` | no |
| Answer quality | `python -m relay.ask --eval medium --core` (also `small`, `large`; `--rest --gap 20`) | about 2K per question |
| Tables from the raw file | `python -m relay.report` | no |
| Where one request's time went | `python -m relay.trace runs/<run>.jsonl [request_id]` | no |

`loadgen` takes `--label NAME` (scenario name in the raw file), `--policy '{"max_attempts":1}'` (any field of
`Policy`), `--standin '{"p_slow":0.2}'` (any field of `StandinConfig`), `--no-coalesce`, `--dup 0.3`.

Every request ever sent appends a row to `results.csv`. Per-request time splits and reply text go to `runs/`.

## Files

| File | Job |
|---|---|
| `relay/config.py` | every number in one place; `GUESS` marks numbers not measured |
| `relay/ingest.py` | PDFs -> pieces with paper, page, section; drops reference lists; keeps tables aligned |
| `relay/retrieve.py` | router, BM25 search, coverage re-rank, evidence score (embeddings optional, off) |
| `relay/control.py` | token-bucket ledger, service levels, admission by predicted wait, retry budget, self-throttling |
| `relay/llm.py` | one streaming call; reads rate-limit headers; never retries |
| `relay/verify.py` | checks quotes, numbers and paper names while the reply streams |
| `relay/pipeline.py` | the five steps, duplicate sharing, extractive fallback |
| `relay/standin.py` | the stand-in model service |
| `relay/loadgen.py` | open-loop load scenarios |
| `relay/probe.py` | small experiments against the real service, and the stand-in fit |
| `relay/recall.py` | token-free retrieval comparison |
| `relay/report.py`, `relay/trace.py` | results tables; one request's latency split |
| `questions.json` | 25 questions with gold answers; `core` marks the 15 used for all three retrieval settings |
| `runs/GRADING_NOTES.md` | how answers were graded and the judgement calls |
| `questions_heldout.json` | 8 questions written after the code was frozen |
| `conversation_history/` | raw assistant transcripts and an index of the prompts (`export.py` refreshes it) |

## How results.csv reads

- `status`: `ok` = a checked model answer or a refusal was delivered; `rate_limited` = a limit stopped the
  model answer and the person got quoted passages instead; `timeout` = the person gave up (nothing on screen
  within their patience) or the hard cap passed; `error` = the provider failed and passages were shown.
- `level` (extra column) says what the person received: full, lean, extractive, refused, none.
- `first_token_ms` is the first *checked* content shown, measured from the scheduled arrival.
  `raw_first_token_ms` is the model's first token before checking.
- `wait_ms`, `work_ms`, `retry_ms` split the time; `coalesced` = 1 means the answer was shared from an
  identical question; `reason` says why a request was degraded or retried.
- `runs/day_ledger.txt` holds today's real token spend, so a new run does not start by believing the day is unspent.
- `answer_grade` is filled only for the `retrieval-*` scenarios (real model). Stand-in rows are never
  graded: the stand-in does not reason.
- `daily-exhausted` is a one-minute real run made while Groq's daily budget was spent.

## What the real service did (all in runs/probes.jsonl)

- Tokens per minute is a bucket of 8,000 that refills 133 tokens a second. A refusal says
  "try again in 3.17s", not "wait a minute".
- Requests per minute is a bucket of 30 that refills one request every 2 s.
- A refused call (429) costs no tokens and no daily request.
- A call abandoned by the client is still charged in full, tokens and request.
- Ten calls at once were all accepted and none was slower than a single call.
- Cached tokens were reported in 1 of 113 probe calls with a repeated opening, so nothing relies on caching.
- Tokens per day is a bucket of 200,000 that refills 2.31 tokens a second. The refusal said "Used 198745 ... try
  again in 13m10.992s"; the same call passed once that wait was over.
- The reply arrives within about 20 ms of its first token.

## Which code produced the numbers

Everything in `results.csv` except the probe rows was measured on 2026-11-12 with the code as submitted,
after a final review fixed 16 things (a crash on a broken reply, a wrong-refusal bug, rows lost when a run is
stopped, a citation attached by word overlap, and smaller ones). The runs made before that review, and the
code as it was, are kept in `runs/before_final_code/` and `runs/relay_before_audit_fixes/`.

## Known gaps

- Requests per day (1,000) was never exhausted; it is known from the header only.
- At the daily refusal my own count of tokens sent was 208,892 against the provider's 198,745; the 5% gap is unexplained.
- The refusal threshold on the local evidence score does nothing useful on this library (the Moshi appendix
  contains trivia questions, so even "capital of France" finds matching words). Refusals come from the model.
- Section detection misses a few headings; one Attention heading is garbled ("3.5 O(n2 · d)").
