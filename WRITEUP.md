# The Relay

All numbers come from runs of the submitted code on 12 Nov and are in `results.csv`; `python -m relay.report`
rebuilds Tables 2a, 2b and 3.

## 1. What I built and why

Five steps: **route** (which paper) → **retrieve** (keyword search; the tool call) → **decide** (branch: refuse /
full / lean / passages only; loop: search once more if the first search is weak) → **draft** (one streamed model
call) → **check** (each quote must be in the cited passage, each number in the cited source; the rest is
dropped before anyone sees it).

The decision that shaped it: **tokens are the scarce thing.** 8,000 tokens a minute at about 2,250 an answer is
3.5 answers a minute. So: one model call per answer, and under pressure the reply gets plainer, never less
true: full → lean (2 passages, 40 words) → passages quoted word for word → refusal. "Fast" means the first
*checked* text on screen. What I gave up is "every reply good".

## 2. The real service and my stand-in

### Table 1: Limits found in the real service (openai/gpt-oss-20b, free tier)

| Limit | Value found | How found | Confidence |
|---|---|---|---|
| Requests per minute | 30, refilling 1 every 2 s | 45 calls 0.3 s apart: 27 accepted, 18 refused "RPM: Limit 30, Used 30", retry-after 2 | high; medium for the refill |
| Tokens per minute | 8,000, refilling 133/s | header says 8000; refused with "Limit 8000, Used 7137, Requested 1286 ... try again in 3.1725s" | high |
| Requests per day | 1,000; reset header grows 86.4 s per request | header only; never exhausted | medium |
| Tokens per day | 200,000, refilling 2.31/s | spent the day until refused: "TPD: Limit 200000, Used 198745, Requested 3086. Please try again in 13m10.992s" (1,831 missing ÷ 791 s = 2.31/s) | high |
| Requests in flight at once | no cap found up to 10 | 10 at once: all accepted, median 548 ms against 798 ms alone | medium |
| What it returns when a limit is hit | HTTP 429 in 50–180 ms naming the limit, used, requested and the wait; `retry-after` in whole seconds; costs no tokens and no request | 22 refusals in `runs/probes.jsonl` | high |
| Time until it recovers | minute: 3.2 s (tokens), 2 s (requests). Day: exactly the named wait; the same call passed once it was over, and an 83-token call passed at once | limit, rpm and daily probes | high |

**Stand-in.** Same buckets, Groq-shaped 429s, 503, slow successes, cut streams, streaming. Fitted from 62
real calls: first token = 0.63 s + 0.28 s per 1,000 prompt tokens; no slowdown under load (none measured). **Least like the real thing:** it cannot reason, so it never refuses, while up to 4 real
requests per run did; and its first token is slower (0.9–2.6 s median against 0.6–0.75 s). Outcomes were
close: same overload, 15 of 17 answered in both; same burst, 11 of 40 against 10 of 40 real.

## 3. Results

Arrivals are random and do not wait for replies; time runs from the scheduled arrival; people give up after
20 s ± 30 % with nothing on screen. Percentiles are p50 / p95 / p99. 30 % of arrivals repeat one of six
questions and may share an answer.

### Table 2a: Latency

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
|---|---|---:|---:|---:|---|---|
| steady | stand-in | 31 | 567 | 3.3 | 1062 / 3206 / 5069 | 1076 / 3223 / 5083 |
| burst (40 extra in 20 s) | stand-in | 45 | 288 | 9.4 | 4 / 16366 / 22379 | 4 / 16380 / 22393 |
| sustained overload | stand-in | 77 | 462 | 10.0 | 4425 / 19652 / 24040 | 4442 / 19670 / 24054 |
| per-minute limit halved at 180 s | stand-in | 17 | 468 | 2.2 | 968 / 6278 / 6278 | 981 / 6292 / 6292 |
| daily budget exhausted | stand-in | 46 | 571 | 4.8 | 10 / 1748 / 3641 | 10 / 1756 / 3655 |
| daily-exhausted | real | 10 | 52 | 11.4 | 7 / 137 / 137 | 7 / 137 / 137 |
| steady-short | real | 9 | 155 | 3.5 | 599 / 6386 / 6386 | 640 / 6472 / 6472 |
| burst-short (40 in 20 s) | real | 40 | 19 | 125.0 | 5 / 10552 / 20686 | 5 / 10624 / 20686 |
| overload-short | real | 17 | 112 | 9.1 | 753 / 16135 / 16135 | 830 / 16232 / 16232 |

### Table 2b: Outcomes

| Scenario | Succeeded (%) | Rate-limited (%) | Timed out or given up (%) | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2268 | $0.21 |
| burst (stand-in) | 35.6 | 64.4 | 0.0 | 0.00 | 3 | 1838 | $0.18 |
| sustained overload (stand-in) | 77.9 | 22.1 | 0.0 | 0.00 | 3 | 1471 | $0.15 |
| per-minute limit halved (stand-in) | 94.1 | 5.9 | 0.0 | 0.00 | 1 | 2358 | $0.22 |
| daily budget exhausted (stand-in) | 43.5 | 56.5 | 0.0 | 0.00 | 1 | 2140 | $0.20 |
| daily-exhausted (real) | 0.0 | 100.0 | 0.0 | 0.00 | 1 | 0 | $0.00 |
| steady-short (real) | 100.0 | 0.0 | 0.0 | 0.11 | 1 | 2341 | $0.21 |
| burst-short (real) | 25.0 | 75.0 | 0.0 | 0.00 | 3 | 1531 | $0.14 |
| overload-short (real) | 88.2 | 11.8 | 0.0 | 0.00 | 2 | 1610 | $0.15 |

"Rate-limited" = the person got quoted passages, not a written answer; not counted as success. The 4–10 ms
medians are those passages; the overload's 48 written answers had a median first token of 11.6 s. Cost is
the paid list price.

**A number I could not have guessed:** a refused call costs nothing, but a call I abandon after 0.2 s is
charged in full (about 1,670 tokens).

**Answer quality** (real model, chosen setting, 23 questions): 13 correct and cited (56.5 %), 2 wrong, 2
refused although answerable, 6 of 6 unanswerable refused; 2,269 tokens per answer. 15 of them were also used
to fix the system; of the 8 fresh ones, 4 of 6 answerable were correct.

## 4. Where the time goes

`overload-short-real-0015` (real): 16,232 ms. Waiting for token budget 15,095 ms, working 1,040 ms before the
first token and 96 ms after, retrying 0. The first token came at 16,135 ms: the delay is **before** it, in
the queue for the per-minute bucket. Every step is stamped; `python -m relay.trace <run file>` prints this.

## 5. When a limit is hit

**Per minute.** The controller keeps its own copy of the token bucket, corrected from each response's
headers, and decides on arrival what fits in each person's patience. *Delayed:* those whose predicted wait
fits. *Weaker answer:* lean, when the bucket is under 35 % (49 of 77 in the overload). *Downgraded, not
dropped:* the rest get passages in about 5 ms (17 of 77). Nobody gave up. When the limit was halved without
notice, the controller read it from the next header and got no 429.

**Per day.** No header shows daily tokens, so the controller counts them, in a file that survives restarts.
With the real budget gone, 10 of 10 people got passages: the first met the 429 (137 ms), the rest took about
7 ms. Never retried. At 2.31 tokens a second, an exhausted day still yields about 3.6 written answers an hour.

## Retrieval: quality versus cost

### Table 3 (same 15 questions per setting, real model)

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|---|---|
| 150 words, 8 pieces, no re-rank | 15 | 60.0 | 6.7 | 26.7 | 2982 | 784 / 1050 | 868 / 1073 |
| 300 words, 4 pieces, coverage re-rank | 15 | 60.0 | 6.7 | 26.7 | 2223 | 638 / 4918 | 720 / 5011 |
| 600 words, 2 pieces, no re-rank | 15 | 46.7 | 13.3 | 26.7 | 1777 | 682 / 1253 | 750 / 1289 |

Times leave out queueing for budget and retrying. The share missing from a row was refused although answerable.

I chose 300 × 4: the same quality as 150 × 8 for 25 % fewer tokens; 600 × 2 is cheapest but lost two
questions. Near the limit, the lean level sends only the top 2 passages. An embedding model and a
cross-encoder did not beat keywords on a token-free check (0.85 against 0.65–0.72 and 0.80), so both are off.

## How this library will mislead a naive retriever

1. **All three say "Transformer"** (28, 92, 134 times). *"Which optimizer and warmup schedule were used to
   train the original Transformer?"* — my first version answered "AdamW", from the Moshi paper.
2. **A paper contradicts itself.** *"What BLEU score does the Transformer get on English-to-French?"* —
   Attention says 41.0 in section 6.1 and 41.8 in its table. An earlier run answered 41.0.
3. **Text that is not the paper's claims.** The Moshi appendix lists trivia questions, so *"What is the capital
   of France?"* matches passages (score 0.67) and cannot be refused on word overlap.

## 6. Retries

One retry at most; only before anything is shown; only if the person will still be waiting; never sooner than
`retry-after`; never for a daily limit, a 413 or a 401.

**Where retrying made things worse** (stand-in, 34 arrivals, 20 % of calls slow enough to time out):

| Arm | Answered (%) | Retries | Written answers | Got passages | Got nothing | First token p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| no retries | 79.4 | 0 | 22 | 7 | 0 | 15337 |
| my policy | 79.4 | 5 | 23 | 6 | 1 | 22347 |
| retry at once, up to 5 times | 61.8 | 54 | 19 | 13 | 0 | 11003 |

Retrying at once sent 54 extra calls and answered 6 fewer people. My policy bought one written answer with 5
retries, but one person got nothing and the slow end grew by 7 s: a timed-out call is still charged, so the
retry pays twice and others queue longer.

## 7. What the person sees

At 3 pm with the daily budget gone, in about 7 ms (this is the real reply from the `daily-exhausted` run):

> Today's budget for written answers is used up; they resume in about 3 minutes. Nothing below is generated:
> these are the passages from the papers that best match your question, quoted exactly.
> "…passage…" [ATT §6.2 Model Variations, p.8]

## 8. What I'm unsure about, what I left out, what I learned

**Least certain:** that people wait 20 s. If they leave after 8 s, most written answers in an overload (median
11.6 s) reach nobody. Also unexplained: at the daily refusal my count was 208,892 tokens, the provider's
198,745. **Not built on purpose:** a second model as fallback; a "similar question" cache.

**New to me:** I thought a rate limit was a counter that resets, so after a 429 you wait a minute, or a day.
Both are buckets that fill all the time: 133 tokens a second and 2.31 a second. The refusal says how long to
wait, and it was right to the second. So each person can be told on arrival whether the wait is 4 seconds or
40, and the 40-second person gets the passages now.

## Your feedback on Vaani

*Draft from reading the home page. Check it against your own visit before you submit.*

Stood out: the page is about what makes a call feel bad (dead air, cutting in, metallic voice), shown as tiny
call snippets, not model names. Confused me: "voice AI harness" in the headline, and two sign-up addresses
(platform.vaani.dev, app.vaanivoice.ai). One change: "~450 ms p90 latency" should say what is timed and over
how many calls.

## How I worked with the assistant

The full, unedited conversation is attached in `conversation_history/`.

**Three prompts that mattered.** (1) The first: read the brief, find the papers on this problem, *read them
page by page*, then plan. The retry budget and timing from the scheduled arrival came from that. (2) "I have
provided the API key ... do the testing": the first real runs broke three assumptions (the limit is not a
one-minute window; answers were cut off; an Attention question was answered from Moshi). (3) "Have you not
made the things hardcoded to solve the errors? Check it": that led to 8 fresh questions, and the score fell.

**One thing it got wrong that I caught.** It scored the system on the same questions it had used to fix it,
and reported that number. My hardcoding question exposed it.

**One decision where I overruled it.** After the load runs it called the work complete. I made it audit its
own code, which found a crash on a broken reply, a wrong-refusal bug and lost results when a run stops; then
I made it measure everything again on the fixed code, including spending the real daily budget.

**What is distinctly mine.** The direction and the checks: papers before design, real measurements, the
hardcoding question, the audit, the re-measurement. The code was written by the assistant, and so were the
questions and the first grading, at my request.
