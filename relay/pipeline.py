"""The five steps: route -> retrieve (tool) -> decide (branch, one loop) -> draft (one model call) -> check.

Only the draft step spends tokens. Under pressure the reply gets less polished, never less truthful:
full answer -> lean answer -> passages quoted verbatim -> honest "the papers do not say / try later".
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .config import DEFAULT_SETTING, RETRIEVAL_SETTINGS, Policy, Provider, RetrievalSetting, approx_tokens
from .control import EXTRACTIVE, FULL, LEAN, Controller
from .llm import CallStats, CallTimeout, RateLimited, ServerError, StreamCut, TooLarge, stream_chat
from .retrieve import Hit, Retrieved, get_index
from .verify import CITE, StreamChecker

SYSTEM = """You answer engineers' questions using ONLY the numbered pieces of three research papers given below the question.
Rules:
- First line: "ANSWERABLE: yes" if the pieces contain the answer, otherwise "ANSWERABLE: no" and stop.
- Then at most 3 supporting quotes, one per line: Q<n> [C<k>] "<words copied exactly from piece C<k>>". Copy 5 to 25 consecutive words. Never paraphrase, shorten with "..." or join separate passages inside a quote.
- Then "ANSWER:" followed by one to three short sentences. Put the supporting quote(s) before each full stop, like [Q1].
- Every number you state must appear in a quote. Each piece is labelled with its paper: do not mix up papers.
- If the pieces only partly answer, answer that part and say what is missing. Never use outside knowledge.
Example:
ANSWERABLE: yes
Q1 [C2] "we trained the base models for a total of 100,000 steps or 12 hours"
ANSWER: The base models were trained for 100,000 steps, about 12 hours [Q1]."""


@dataclass
class Result:
    question: str
    status: str = "ok"            # ok | rate_limited | timeout | error
    level: str = FULL             # full | lean | extractive | refused | deferred
    text: str = ""
    citations: list[str] = field(default_factory=list)
    first_token_ms: float | None = None      # first VERIFIED content shown to the person
    raw_first_token_ms: float | None = None  # first token from the model, before checking
    total_ms: float = 0.0
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0     # thinking tokens: part of tokens_out, never shown
    wait_ms: float = 0.0          # queued for budget
    work_ms: float = 0.0          # local steps + first model attempt
    retry_ms: float = 0.0         # backoff sleeps + repeated attempts
    spans: list[tuple[str, str, float, float]] = field(default_factory=list)  # (kind, name, start_ms, end_ms)
    reason: str = ""
    evidence: float = 0.0
    dropped_sentences: int = 0
    bad_quotes: int = 0
    coalesced: bool = False
    queue_len: int = 0            # how many were queued for budget when this request joined
    retrieval_setting: str = ""
    raw: str = ""                 # the model's unchecked output, kept for grading and debugging


Emit = Callable[[str], None]


def why_short(err: Exception) -> str:
    return f"429 {err.kind}" if isinstance(err, RateLimited) else type(err).__name__


def _key(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", q.lower())).strip()


class Pipeline:
    def __init__(self, provider: Provider, controller: Controller | None = None,
                 setting: RetrievalSetting | str = DEFAULT_SETTING, policy: Policy | None = None,
                 tool_fault: Callable[[], Awaitable[None]] | None = None):
        self.provider = provider
        self.policy = policy or (controller.policy if controller else Policy())
        self.ctl = controller or Controller(policy=self.policy)
        self.setting = RETRIEVAL_SETTINGS[setting] if isinstance(setting, str) else setting
        self.index = get_index(self.setting)
        self.tool_fault = tool_fault
        self._inflight: dict[str, asyncio.Future] = {}
        self._cache: dict[str, Result] = {}

    # ---- public ----------------------------------------------------------
    async def answer(self, question: str, emit: Emit | None = None, patience_s: float | None = None,
                     arrived: float | None = None, share_key: str | None = None) -> Result:
        """`arrived` is when the person asked (monotonic clock); all times are measured from it.
        `share_key` replaces the question text as the key for sharing answers (the load generator uses it
        to make most arrivals distinct questions, without needing hundreds of hand-written ones)."""
        t0 = arrived if arrived is not None else time.monotonic()
        patience = patience_s if patience_s is not None else self.policy.patience_s
        key = share_key or _key(question)
        if self.policy.coalesce:
            if key in self._cache:
                return self._copy_for_follower(self._cache[key], t0, emit)
            if key in self._inflight:
                try:
                    leader = await asyncio.shield(self._inflight[key])
                    return self._copy_for_follower(leader, t0, emit)
                except Exception:
                    pass                      # leader failed: do our own work
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
        try:
            res = await self._run(question, emit or (lambda s: None), t0, patience)
            if self.policy.coalesce:
                if res.status == "ok" and res.level in (FULL, LEAN, "refused"):
                    self._cache[key] = res
                fut.set_result(res)
            return res
        except BaseException as e:
            if self.policy.coalesce and not fut.done():
                fut.set_exception(e if isinstance(e, Exception) else RuntimeError("cancelled"))
                fut.exception()               # mark as retrieved
            raise
        finally:
            if self.policy.coalesce and self._inflight.get(key) is fut:
                del self._inflight[key]

    def _copy_for_follower(self, r: Result, t0: float, emit: Emit | None) -> Result:
        ms = (time.monotonic() - t0) * 1000
        if emit:
            emit(r.text)
        return Result(question=r.question, status=r.status, level=r.level, text=r.text, citations=r.citations,
                      first_token_ms=ms, raw_first_token_ms=ms, total_ms=ms, coalesced=True, evidence=r.evidence,
                      reason="shared an identical question's answer", retrieval_setting=r.retrieval_setting)

    # ---- the steps -------------------------------------------------------
    async def _run(self, question: str, emit: Emit, t0: float, patience: float) -> Result:
        res = Result(question=question, retrieval_setting=self.setting.name)
        ms = lambda: (time.monotonic() - t0) * 1000

        def span(kind: str, name: str, start: float):
            end = ms()
            res.spans.append((kind, name, round(start, 1), round(end, 1)))
            setattr(res, f"{kind}_ms", getattr(res, f"{kind}_ms") + end - start)

        def shown(text: str):
            if res.first_token_ms is None:
                res.first_token_ms = ms()
            emit(text)

        def done(status: str, level: str, reason: str = "") -> Result:
            res.status, res.level, res.total_ms = status, level, ms()
            res.reason = reason or res.reason
            return res

        # 1) route + 2) retrieve (the tool call) + 3) decide, with one rewrite loop
        s = ms()
        route = self.index.route(question)
        try:
            got = await self._tool_search(question, route)
            if got.evidence < self.policy.retry_search_below:
                again = await self._tool_search(self.index.rewrite(question, route), route)
                if again.evidence > got.evidence:
                    got = again
        except asyncio.TimeoutError:
            span("work", "retrieve", s)
            shown("The search over the papers timed out, so I cannot answer reliably right now. Please ask again.")
            return done("error", "deferred", "retrieval tool timed out twice")
        res.evidence = got.evidence
        span("work", "route+retrieve+decide", s)
        if not got.hits or got.evidence < self.policy.refuse_below:
            res.text = ("The three papers in this library do not appear to cover that, so I will not guess. "
                        "I can answer questions about Attention Is All You Need, Moshi, and Dynamic Chunking (H-Net).")
            shown(res.text)
            return done("ok", "refused", f"evidence {got.evidence:.2f} below refusal threshold")

        # 4) admission: which service level can this person get in the time they will wait?
        time_left = patience - ms() / 1000
        hits_full = got.hits
        hits_lean = got.hits[: self.policy.lean_k]
        tok = lambda hits, out: approx_tokens(SYSTEM + question + "".join(h.piece.text + h.piece.header() for h in hits)) + out
        dec = self.ctl.decide(tok(hits_full, self.policy.out_tokens_full), tok(hits_lean, self.policy.out_tokens_lean), time_left)
        if dec.level == EXTRACTIVE:
            return self._extractive(res, got.hits, shown, done, dec.reason)
        hits = hits_full if dec.level == FULL else hits_lean
        max_out = self.policy.out_tokens_full if dec.level == FULL else self.policy.out_tokens_lean
        res.level = dec.level

        # 5) draft with the model, checking as it streams
        messages = self._messages(question, hits, dec.level)
        attempt = 0
        while True:
            attempt += 1
            kind = "work" if attempt == 1 else "retry"
            s = ms()
            res.queue_len = max(res.queue_len, len(self.ctl._waiters) + 1)
            reservation = await self.ctl.acquire(tok(hits, max_out), priority=0 if attempt > 1 else 1,
                                                 give_up_at=t0 + patience if res.first_token_ms is None else None)
            span("wait" if attempt == 1 else "retry", "queue for token budget", s)
            if reservation is None:
                return self._extractive(res, got.hits, shown, done, "queue wait exceeded patience")
            stats = CallStats()
            checker = StreamChecker(question, [h.piece.text for h in hits], [h.piece.paper for h in hits])
            s = ms()
            err: Exception | None = None
            try:
                await self._draft(messages, max_out, stats, checker, hits, res, shown, ms)
            except (RateLimited, TooLarge, ServerError, CallTimeout, StreamCut) as e:
                err = e
            span(kind, f"model call #{attempt}", s)
            accepted = stats.status == 200
            # What did this call cost? The provider's own count if we got one. A call we gave up on (timeout,
            # or a stream that died) is still charged in full: measured on Groq, runs/probes.jsonl "abandon-*".
            # So the estimate stays on the books. Only a clear refusal, or never connecting, costs nothing.
            never_sent = any(k in str(err) for k in ("ConnectError", "ConnectTimeout", "PoolTimeout"))
            charged_anyway = isinstance(err, (CallTimeout, StreamCut)) and not never_sent
            used = (stats.tokens_in + stats.tokens_out) if (stats.tokens_in or stats.tokens_out) else (
                None if (accepted or charged_anyway) else 0)
            self.ctl.settle(reservation, used, accepted and err is None)
            self.ctl.observe_headers(stats.headers)
            res.tokens_in += stats.tokens_in
            res.tokens_out += stats.tokens_out
            res.cached_tokens += stats.cached_tokens
            res.reasoning_tokens += stats.reasoning_tokens
            res.dropped_sentences += checker.sentences_dropped
            res.bad_quotes += checker.bad_quotes
            if err is None:
                break
            if isinstance(err, RateLimited) and not self.policy.naive:
                self.ctl.on_rate_limited(err)
            # Retry only if: nothing has been shown yet, the error can go away, the budget allows it,
            # and the person will still be there when the retry lands.
            started_showing = res.first_token_ms is not None
            retry_after = err.retry_after if isinstance(err, RateLimited) else None
            day = isinstance(err, RateLimited) and err.kind.endswith("per_day")
            pause = self.ctl.backoff(attempt, retry_after)
            fits = self.policy.naive or ms() / 1000 + pause + 1.0 < patience
            if (not started_showing and not day and not isinstance(err, TooLarge) and fits
                    and self.ctl.may_retry(attempt)):
                s = ms()
                await asyncio.sleep(pause)
                span("retry", f"backoff after {type(err).__name__}", s)
                res.retries += 1
                res.reason = f"retried once after {why_short(err)}, waited {pause:.1f}s"
                continue
            if started_showing:
                shown("\n[The reply was cut short. Everything above was checked against the papers.]")
                res.text += "\n[cut short]"
                return done("error", res.level, f"stream failed after first token: {type(err).__name__}")
            why = {RateLimited: "rate limit", TooLarge: "request too large for the per-minute limit",
                   ServerError: "provider error", CallTimeout: "provider too slow", StreamCut: "connection cut"}[type(err)]
            if isinstance(err, (StreamCut, CallTimeout, ServerError)):
                why += f" ({err})"          # e.g. ConnectError means we never reached the provider at all
            return self._extractive(res, got.hits, shown, done, why,
                                    status="rate_limited" if isinstance(err, (RateLimited, TooLarge)) else "error")

        if checker.refused or (not checker.sentences_ok and not checker.quotes):
            res.text = "The papers in this library do not say, so I will not guess."
            shown(res.text)
            return done("ok", "refused", "model judged the pieces insufficient" if checker.refused else "nothing in the draft survived checking")
        if not checker.sentences_ok:
            # quotes were real but no sentence survived: give the quotes themselves
            return done("ok", "extractive", "no answer sentence passed checking; verified quotes shown")
        return done("ok", res.level)

    async def _tool_search(self, query: str, route: dict) -> Retrieved:
        """The tool call. Local, but treated like any tool: it has a timeout and one retry."""
        for attempt in (1, 2):
            try:
                async def call():
                    if self.tool_fault:
                        await self.tool_fault()
                    return self.index.search(query, route=route)
                return await asyncio.wait_for(call(), self.policy.tool_timeout_s)
            except asyncio.TimeoutError:
                if attempt == 2:
                    raise
        raise asyncio.TimeoutError

    def _messages(self, question: str, hits: list[Hit], level: str) -> list[dict]:
        ctx = "\n\n".join(f"[C{i}] ({h.piece.header()})\n{h.piece.text}" for i, h in enumerate(hits, start=1))
        # beLLMan: explicit word caps are obeyed
        cap = "" if level == FULL else "\nUse one or two quotes and answer in 40 words or fewer."
        return [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Question: {question}{cap}\n\nPieces:\n{ctx}"}]

    async def _draft(self, messages, max_out, stats, checker, hits, res, shown, ms):
        cite = lambda idx: "; ".join(self._cite(hits[i - 1]) for i in idx)
        async for piece in stream_chat(self.provider, messages, max_out, stats,
                                       self.policy.first_byte_timeout_s, self.policy.call_timeout_s):
            if res.raw_first_token_ms is None:
                res.raw_first_token_ms = ms()
            res.raw += piece
            for ev in checker.feed(piece):
                self._show(ev, cite, res, shown)
            if checker.refused:
                return                      # stop reading: saves output tokens
        for ev in checker.finish():
            self._show(ev, cite, res, shown)

    def _show(self, ev, cite, res: Result, shown):
        if ev.kind == "quote":
            line = f'Evidence: "{ev.text}" [{cite(ev.pieces)}]\n'
        elif ev.kind == "sentence":
            line = CITE.sub("", ev.text).strip() + f" [{cite(ev.pieces)}]\n"
        else:
            return
        for c in cite(ev.pieces).split("; "):
            if c not in res.citations:
                res.citations.append(c)
        res.text += line
        shown(line)

    @staticmethod
    def _cite(h: Hit) -> str:
        p = h.piece
        return f"{p.paper} {p.section + ', ' if p.section else ''}p.{p.page}"

    def _extractive(self, res: Result, hits: list[Hit], shown, done, reason: str, status: str = "rate_limited") -> Result:
        """No model call: quote the best passages word for word, with where they came from."""
        if reason.startswith("daily"):
            # Say what happened, when it ends, and what the person can still rely on.
            left = self.ctl.day_resume_in()
            when = f"in about {max(1, round(left / 60))} minutes" if left else "later today"
            head = (f"Today's budget for written answers is used up; they resume {when}. Nothing below is generated: "
                    f"these are the passages from the papers that best match your question, quoted exactly.\n")
        else:
            head = f"I can't write a summary right now ({reason}). These passages from the papers are the closest match, quoted exactly:\n"
        lines = [head]
        for h in hits[:2]:
            words = h.piece.text.split()
            lines.append(f'"{" ".join(words[:70])}{" ..." if len(words) > 70 else ""}" [{self._cite(h)}]\n')
            res.citations.append(self._cite(h))
        res.text = "".join(lines)
        shown(res.text)
        return done(status, "extractive", reason)
