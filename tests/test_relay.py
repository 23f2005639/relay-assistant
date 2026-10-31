import asyncio
import time

import pytest

from relay.config import Limits, Policy, Provider, RETRIEVAL_SETTINGS
from relay.control import EXTRACTIVE, FULL, LEAN, Controller
from relay.llm import RateLimited, limit_kind, parse_duration
from relay.pipeline import Pipeline
from relay.retrieve import get_index
from relay.standin import StandinConfig, start
from relay.verify import StreamChecker, quote_in

PIECE = ("We trained the base models for a total of 100,000 steps or 12 hours. For our big models, "
         "step time was 1.0 seconds. The big models were trained for 300,000 steps (3.5 days).")


# ---- verify -------------------------------------------------------------
def test_quote_must_exist():
    assert quote_in("trained the base models for a total of 100,000 steps", PIECE)
    assert quote_in("trained the base models  for a\ntotal of 100,000 steps", PIECE)      # whitespace differences are fine
    assert not quote_in("trained the base models for a total of 200,000 steps or 12 days and more", PIECE)
    assert not quote_in("100,000 steps", PIECE)                                           # too short to prove anything


def run_checker(text, chunk=7):
    c = StreamChecker("How long were the base models trained?", [PIECE])
    events = []
    for i in range(0, len(text), chunk):          # feed in small bits, like a stream
        events += c.feed(text[i:i + chunk])
    return c, events + c.finish()


def test_good_answer_passes():
    c, ev = run_checker('ANSWERABLE: yes\nQ1 [C1] "We trained the base models for a total of 100,000 steps or 12 hours"\n'
                        "ANSWER: The base models were trained for 100,000 steps, about 12 hours [Q1].")
    assert [e.kind for e in ev] == ["quote", "sentence"]
    assert c.sentences_ok == 1 and c.sentences_dropped == 0


def test_invented_quote_and_number_are_dropped():
    c, ev = run_checker('ANSWERABLE: yes\nQ1 [C1] "The base models were trained for nine days on sixteen GPUs in total"\n'
                        "ANSWER: Training took 9 days on 16 GPUs [Q1].")
    assert [e.kind for e in ev] == ["bad_quote", "dropped"]


def test_wrong_number_with_real_quote_is_dropped():
    c, ev = run_checker('ANSWERABLE: yes\nQ1 [C1] "We trained the base models for a total of 100,000 steps or 12 hours"\n'
                        "ANSWER: The base models were trained for 250,000 steps [Q1].")
    assert ev[-1].kind == "dropped" and "250000" in ev[-1].note


def test_citation_after_the_full_stop_stays_with_its_sentence():
    # how the real model writes it; an earlier version dropped the sentence and showed an empty one
    c, ev = run_checker('ANSWERABLE: yes\nQ1 [C1] "We trained the base models for a total of 100,000 steps or 12 hours"\n'
                        "ANSWER: The base models were trained for 100,000 steps. [Q1] That took about 12 hours. [Q1]")
    assert [e.kind for e in ev] == ["quote", "sentence", "sentence"] and c.sentences_dropped == 0


def test_sentence_about_one_paper_cannot_cite_another():
    hnet = "The Transformer is the default sequence mixing architecture in the main network of the model."
    c = StreamChecker("What role does a Transformer play in Moshi?", [hnet], ["HNET"])
    ev = c.feed('ANSWERABLE: yes\nQ1 [C1] "The Transformer is the default sequence mixing architecture in the main network"\n'
                "ANSWER: In Moshi, the Transformer is the default architecture of the main network [Q1].") + c.finish()
    assert ev[-1].kind == "dropped" and c.wrong_paper == 1
    c = StreamChecker("What does H-Net use?", [hnet], ["HNET"])
    ev = c.feed('ANSWERABLE: yes\nQ1 [C1] "The Transformer is the default sequence mixing architecture in the main network"\n'
                "ANSWER: In H\u2011Net, the Transformer is the default architecture of the main network [Q1].") + c.finish()
    assert ev[-1].kind == "sentence"


def test_refusal_detected_early():
    c, ev = run_checker("ANSWERABLE: no\n")
    assert c.refused and ev[0].kind == "refuse"
    c, ev = run_checker('ANSWERABLE: yes, but not all of it\nQ1 [C1] "We trained the base models for a total of 100,000 steps or 12 hours"\n'
                        "ANSWER: The base models were trained for 100,000 steps [Q1].")
    assert not c.refused and ev[-1].kind == "sentence"       # "not" contains "no"; that is still a yes


def test_sentence_without_a_verified_quote_is_dropped():
    c, ev = run_checker("ANSWERABLE: yes\nANSWER: The base models were trained for a total of 100,000 steps or 12 hours.")
    assert ev[-1].kind == "dropped"      # same words as the passage, but no checked quote: not shown


# ---- controller ---------------------------------------------------------
def test_wait_for_and_levels():
    ctl = Controller(Limits(tpm=8000), Policy())
    assert ctl.decide(2500, 1200, time_left_s=20).level == FULL
    now = time.monotonic()
    ctl.spend(6500, now)                                                 # bucket holds 1500 of 8000
    assert ctl.wait_for(2500, now) == pytest.approx(1000 / (8000 / 60), abs=0.2)   # refills 133 tokens a second
    assert ctl.decide(2500, 1200, time_left_s=20).level == LEAN          # little headroom: lean
    ctl.spend(4500)                                                      # bucket 3000 in debt
    assert ctl.decide(2500, 1200, time_left_s=20).level == EXTRACTIVE    # nothing fits within patience


def test_daily_budget_and_learning_from_429():
    ctl = Controller(Limits(tpm=8000, tpd=10_000), Policy())
    ctl._day.append([time.monotonic(), 9_900, True])
    d = ctl.decide(2500, 1200, 20)
    assert d.level == EXTRACTIVE and "daily" in d.reason
    ctl2 = Controller(Limits(tpm=8000), Policy())
    ctl2.on_rate_limited(RateLimited(7.0, "tokens_per_minute", "Rate limit reached ... (TPM): Limit 4000, Used 3900", {}))
    assert ctl2.limits.tpm == 4000 and ctl2.blocked_until > time.monotonic() + 6


def test_retry_bucket_runs_dry():
    ctl = Controller(policy=Policy(retry_bucket_cap=2))
    assert ctl.may_retry(1) and ctl.may_retry(1)
    assert not ctl.may_retry(1)          # bucket empty: failures can no longer multiply load
    assert not Controller().may_retry(2)  # second attempt was the last


def test_limit_kind_reads_the_real_messages():
    # copied from runs/probes.jsonl; an earlier version called the first one a token limit
    assert limit_kind("on requests per minute (RPM): Limit 30, Used 30, Requested 1. Please try again in 2s. "
                      "Need more tokens? Upgrade") == "requests_per_minute"
    assert limit_kind("on tokens per minute (TPM): Limit 8000, Used 7137, Requested 1286.") == "tokens_per_minute"
    assert limit_kind("on tokens per day (TPD): Limit 200000, Used 199000, Requested 2500.") == "tokens_per_day"


def test_request_bigger_than_the_limit_does_not_wait(tmp_path):
    ctl = Controller(Limits(tpm=1000), Policy())
    assert asyncio.run(ctl.acquire(2000)) is None          # no give-up time set: must still return at once


def test_days_spend_survives_a_restart(tmp_path):
    f = tmp_path / "day.txt"
    ctl = Controller(day_file=f)
    ctl.settle(asyncio.run(ctl.acquire(2000)), 2100, True)
    assert Controller(day_file=f).day_tokens(time.monotonic()) == 2100


def test_parse_duration():
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration("3") == 3.0


# ---- retrieval ----------------------------------------------------------
def test_reference_lists_are_not_indexed_and_tables_are():
    ix = get_index(RETRIEVAL_SETTINGS["medium"])
    assert not any("arXiv preprint arXiv:1607.06450" in p.text for p in ix.pieces)
    assert any(p.kind == "table" and "28.4" in p.text for p in ix.pieces if p.paper == "ATT")


def test_route_and_search():
    ix = get_index(RETRIEVAL_SETTINGS["medium"])
    assert max(ix.route("What is Moshi's theoretical latency?").items(), key=lambda kv: kv[1])[0] == "MOSHI"
    r = ix.search("What BLEU score does the big Transformer get on English-to-German?")
    assert any("28.4" in h.piece.text for h in r.hits)
    assert ix.search("What is the best pizza topping?").evidence < 0.34


# ---- end to end against the stand-in ------------------------------------
def test_pipeline_end_to_end():
    async def go():
        _, runner = await start(StandinConfig(ttft_median_s=0.02, tpm=3000), 8097)
        try:
            pipe = Pipeline(Provider.standin(8097), Controller(Limits(), Policy(coalesce=False)), "medium")
            shown = []
            ok = await pipe.answer("What is the theoretical latency of Moshi?", emit=shown.append)
            assert ok.status == "ok" and ok.level in (FULL, LEAN) and ok.citations and shown
            assert ok.first_token_ms <= ok.total_ms and ok.tokens_in > 0
            no = await pipe.answer("What is the best pizza topping?")
            assert no.level == "refused" and no.tokens_in == 0             # refused without spending tokens
            # the stand-in allows 3000 tokens a minute; the controller thought 8000 and must adapt, not fail
            third = await pipe.answer("What frame rate does the Mimi codec operate at?", patience_s=5)
            assert third.level == EXTRACTIVE and third.status == "rate_limited" and third.text
            assert pipe.ctl.limits.tpm == 3000
        finally:
            await runner.cleanup()
    asyncio.run(go())


def test_any_provider_failure_ends_in_passages(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("401: invalid api key")
        yield                                                  # makes this an async generator, like stream_chat
    monkeypatch.setattr("relay.pipeline.stream_chat", broken)
    pipe = Pipeline(Provider.standin(1), Controller(policy=Policy(coalesce=False)), "medium")
    shown = []
    r = asyncio.run(pipe.answer("What is the theoretical latency of Moshi?", emit=shown.append))
    assert r.level == EXTRACTIVE and r.status == "error" and r.retries == 0 and shown and "401" in r.reason


def test_search_tool_timeout_is_reported_not_hidden():
    async def hang():
        await asyncio.sleep(1)
    pipe = Pipeline(Provider.standin(1), Controller(policy=Policy(tool_timeout_s=0.05)), "medium", tool_fault=hang)
    r = asyncio.run(pipe.answer("What is the theoretical latency of Moshi?"))
    assert r.status == "error" and r.level == "deferred" and "timed out" in r.text
