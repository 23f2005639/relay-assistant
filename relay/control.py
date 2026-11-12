"""The controller: decides who gets a model call, when, and at what service level.

It keeps its own ledger of tokens (the provider's headers do not report the daily token count),
learns the real limits from headers and 429 bodies, and owns every retry decision.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import Limits, Policy
from .llm import RateLimited

FULL, LEAN, EXTRACTIVE = "full", "lean", "extractive"


@dataclass
class Decision:
    level: str
    reason: str


class Controller:
    def __init__(self, limits: Limits | None = None, policy: Policy | None = None, seed: int = 0,
                 day_file: Path | None = None):
        self.limits = limits or Limits()
        self.day_file = day_file                 # real provider only: today's spend must survive a restart
        self.policy = policy or Policy()
        self.rng = random.Random(seed)
        # Measured on Groq (runs/probes.jsonl, probe-limit): the per-minute token limit is a bucket that
        # refills continuously at tpm/60 tokens a second, not a window that empties once a minute.
        self._avail = float(self.limits.tpm)     # tokens we believe are in the provider's bucket
        self._avail_t = time.monotonic()
        self._minute: deque[float] = deque()     # request start times, for the requests-per-minute limit
        self._day: deque[list] = deque()
        self.blocked_until = 0.0                 # minute-level 429 told us to wait until then
        self.day_blocked_until = 0.0             # day-level 429
        self._waiters: list = []
        self._seq = itertools.count()
        self._queued_tokens = 0
        self.peak_queue = 0
        self.retry_tokens = self.policy.retry_bucket_cap
        self._req: deque[float] = deque()        # adaptive throttle: attempts
        self._acc: deque[float] = deque()        # adaptive throttle: accepted by the provider
        self.events: list[tuple[float, str]] = []  # limit changes etc., for the write-up
        self.trace: list[dict] = []              # one entry per model call: our ledger next to the provider's header
        if day_file and day_file.exists():
            # shortcut: probe calls and cancelled calls are not in this file, add them if the daily count must be exact
            for line in day_file.read_text().splitlines():
                wall, tokens = line.split()
                self._day.append([time.monotonic() - (time.time() - float(wall)), int(tokens), True])

    # ---- ledger ----------------------------------------------------------
    def _prune(self, now: float):
        while self._minute and now - self._minute[0] >= 60.0:
            self._minute.popleft()
        self._avail = min(float(self.limits.tpm), self._avail + (now - self._avail_t) * self.limits.tpm / 60.0)
        self._avail_t = now
        while self._day and now - self._day[0][0] >= self.limits.day_seconds:
            self._day.popleft()

    def minute_tokens(self, now: float) -> int:
        """Tokens currently missing from the bucket."""
        self._prune(now)
        return int(self.limits.tpm - self._avail)

    def spend(self, tokens: float, now: float | None = None):
        """Take tokens out of the bucket."""
        self._prune(time.monotonic() if now is None else now)
        self._avail -= tokens

    def day_tokens(self, now: float) -> int:
        self._prune(now)
        return sum(e[1] for e in self._day)

    def day_requests(self, now: float) -> int:
        self._prune(now)
        return sum(1 for e in self._day if e[2])

    def day_left_frac(self, now: float) -> float:
        t = 1 - self.day_tokens(now) / self.limits.tpd
        r = 1 - self.day_requests(now) / self.limits.rpd
        return max(0.0, min(t, r))

    def wait_for(self, tokens: int, now: float) -> float:
        """Seconds until the bucket holds `tokens`."""
        self._prune(now)
        wait = max(0.0, self.blocked_until - now)
        short = tokens - self._avail
        if short > 0:
            wait = max(wait, short / (self.limits.tpm / 60.0))
        reqs = list(self._minute)
        if len(reqs) >= self.limits.rpm:
            wait = max(wait, reqs[len(reqs) - self.limits.rpm] + 60.0 - now)
        return wait

    def day_resume_in(self) -> float | None:
        """Seconds until the provider said the daily budget frees up, if it has told us."""
        left = self.day_blocked_until - time.monotonic()
        return left if left > 0 else None

    # ---- admission -------------------------------------------------------
    def decide(self, tokens_full: int, tokens_lean: int, time_left_s: float, service_s: float = 1.5) -> Decision:
        """Pick a service level for a new request, given how long its person will still wait."""
        now = time.monotonic()
        p = self.policy
        if p.naive:
            return Decision(FULL, "naive arm: always send")
        if tokens_lean > self.limits.tpm:
            return Decision(EXTRACTIVE, "request larger than the per-minute limit")
        if now < self.day_blocked_until or self.day_left_frac(now) <= p.day_reserve_frac:
            return Decision(EXTRACTIVE, "daily budget exhausted")
        if self.rng.random() < self.reject_prob(now):
            return Decision(EXTRACTIVE, "self-throttled: provider is rejecting")
        ahead = self._queued_tokens
        w_full = self.wait_for(ahead + tokens_full, now)
        if p.force_full:
            return Decision(FULL, "quality run: full answer forced")
        minute_left = 1 - (self.minute_tokens(now) + ahead) / self.limits.tpm
        if minute_left >= p.lean_below_frac and w_full + service_s <= time_left_s and tokens_full <= self.limits.tpm:
            return Decision(FULL, "headroom")
        w_lean = self.wait_for(ahead + tokens_lean, now)
        if w_lean + service_s <= time_left_s:
            return Decision(LEAN, "per-minute limit close")
        return Decision(EXTRACTIVE, f"predicted wait {w_lean:.1f}s exceeds patience")

    async def acquire(self, tokens: int, priority: int = 1, give_up_at: float | None = None) -> list | None:
        """Wait for our turn and for room in the minute window. Lower priority number goes first.
        Returns a reservation, or None if `give_up_at` passes first."""
        me = (priority, next(self._seq))
        heapq.heappush(self._waiters, me)
        self._queued_tokens += tokens
        self.peak_queue = max(self.peak_queue, len(self._waiters))
        try:
            while True:
                now = time.monotonic()
                if tokens > self.limits.tpm:
                    return None             # can never fit, e.g. the limit was cut while this request queued
                if self._waiters[0] == me or self.policy.naive:
                    w = 0.0 if self.policy.naive else self.wait_for(tokens, now)
                    if w <= 0:
                        self._avail -= tokens
                        self._minute.append(now)
                        day_entry = [now, tokens, True]
                        self._day.append(day_entry)
                        self._req.append(now)
                        return [tokens, day_entry]
                else:
                    w = 0.05
                if give_up_at is not None and now + w > give_up_at:
                    return None
                await asyncio.sleep(min(max(w, 0.01), 0.25))
        finally:
            self._waiters.remove(me)
            heapq.heapify(self._waiters)
            self._queued_tokens -= tokens

    def settle(self, reservation: list, actual_tokens: int | None, accepted: bool):
        """Replace the estimate with what the provider actually counted."""
        if actual_tokens is not None:
            self._prune(time.monotonic())
            self._avail = min(float(self.limits.tpm), self._avail + reservation[0] - actual_tokens)
            reservation[1][1] = actual_tokens
        if self.day_file and reservation[1][1]:
            with self.day_file.open("a") as fh:
                fh.write(f"{time.time():.0f} {reservation[1][1]}\n")
        if accepted:
            self._acc.append(time.monotonic())
            self.retry_tokens = min(self.policy.retry_bucket_cap, self.retry_tokens + self.policy.retry_deposit)

    # ---- learning the limits --------------------------------------------
    def observe_headers(self, h: dict):
        now = time.monotonic()
        lim = h.get("x-ratelimit-limit-tokens")
        if lim and lim.isdigit() and int(lim) != self.limits.tpm:
            self.events.append((now, f"tpm {self.limits.tpm} -> {lim} (header)"))
            self.limits.tpm = int(lim)
        lim = h.get("x-ratelimit-limit-requests")
        if lim and lim.isdigit() and int(lim) != self.limits.rpd:
            self.events.append((now, f"rpd {self.limits.rpd} -> {lim} (header)"))
            self.limits.rpd = int(lim)
        rem = h.get("x-ratelimit-remaining-tokens")
        if rem and rem.replace(".", "").isdigit():
            self._prune(now)
            self.trace.append({"ours": round(self._avail), "provider": int(float(rem))})
            if float(rem) < self._avail - 200:   # provider has counted more than we have: trust the provider
                self._avail = float(rem)

    def on_rate_limited(self, e: RateLimited):
        now = time.monotonic()
        m = re.search(r"Limit (\d+)", e.body)
        if e.kind.endswith("per_day"):
            self.day_blocked_until = now + e.retry_after
            self.events.append((now, f"daily limit hit ({e.kind}), retry after {e.retry_after:.0f}s"))
        else:
            self.blocked_until = max(self.blocked_until, now + e.retry_after)
            if m and e.kind == "tokens_per_minute" and int(m.group(1)) != self.limits.tpm:
                self.events.append((now, f"tpm {self.limits.tpm} -> {m.group(1)} (429 body)"))
                self.limits.tpm = int(m.group(1))
        self.observe_headers(e.headers)

    # ---- retries (one layer, one budget) -----------------------------------
    def may_retry(self, attempt: int) -> bool:
        if self.policy.naive:
            return attempt < self.policy.max_attempts
        if attempt >= self.policy.max_attempts or self.retry_tokens < 1.0:
            return False
        self.retry_tokens -= 1.0
        return True

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        if self.policy.naive:
            return 0.05
        base = retry_after if retry_after is not None else 0.5 * (2 ** (attempt - 1))
        return base + self.rng.uniform(0, 0.5 * max(base, 0.2))     # jitter de-synchronises the herd

    # ---- adaptive throttling (Google SRE book, ch. 21) ---------------------
    def reject_prob(self, now: float) -> float:
        w = self.policy.throttle_window_s
        while self._req and now - self._req[0] > w:
            self._req.popleft()
        while self._acc and now - self._acc[0] > w:
            self._acc.popleft()
        r, a = len(self._req), len(self._acc)
        if r < 10:          # too few attempts to judge; one early failure must not shut the door
            return 0.0
        return max(0.0, (r - self.policy.throttle_k * a) / (r + 1))

