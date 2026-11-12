"""Stand-in model service: an OpenAI-compatible endpoint we control.

It can be slow, fail, refuse at a limit (429 with retry-after and Groq-style headers), slow down
as load rises, and stream its reply piece by piece. It does NOT reason: it copies a quote from the
first piece in the prompt, so answer quality from stand-in runs is never reported.

Run:  python -m relay.standin --port 8089
Change behaviour while running:  POST /admin/config  {"tpm": 4000}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import time
from collections import deque
from dataclasses import asdict, dataclass

from aiohttp import web

from .config import approx_tokens


@dataclass
class StandinConfig:
    # limits (defaults = Groq free tier docs for gpt-oss-20b)
    rpm: int = 30
    tpm: int = 8000
    rpd: int = 1000
    tpd: int = 200_000
    day_seconds: float = 86_400.0
    max_inflight: int = 0           # 0 = no cap (GUESS: unknown for the real service)
    # latency. Placeholders until the probe run calibrates them from the real service.
    ttft_median_s: float = 0.35     # GUESS
    ttft_sigma: float = 0.45        # GUESS: lognormal shape
    ttft_per_1k_in_s: float = 0.0   # extra time to first token per 1,000 prompt tokens (fitted from real calls)
    tokens_per_s: float = 400.0     # GUESS
    load_slowdown: float = 0.08     # each in-flight request adds 8% to latency  (GUESS)
    extra_out_tokens: int = 0       # charged but not streamed: the real model's thinking and longer answers
    chars_per_token: float = 3.6    # how the stand-in counts prompt tokens
    # faults
    p_error: float = 0.0            # immediate 503
    p_slow: float = 0.0             # succeeds, but first token takes slow_factor times longer
    slow_factor: float = 15.0
    p_cut: float = 0.0              # stream dies halfway
    seed: int = 1


class Standin:
    def __init__(self, cfg: StandinConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        # Like the real service (measured): a token bucket refilling at tpm/60 a second.
        self.avail = float(cfg.tpm)
        self.avail_t = time.monotonic()
        self.minute: deque[float] = deque()            # request times, for requests per minute
        self.day: deque[tuple[float, int]] = deque()
        self.inflight = 0
        self.frozen_until = 0.0
        self.stats = {"requests": 0, "ok": 0, "429": 0, "413": 0, "503": 0, "cut": 0, "peak_inflight": 0}

    def _prune(self, now):
        while self.minute and now - self.minute[0] >= 60:
            self.minute.popleft()
        self.avail = min(float(self.cfg.tpm), self.avail + (now - self.avail_t) * self.cfg.tpm / 60.0)
        self.avail_t = now
        while self.day and now - self.day[0][0] >= self.cfg.day_seconds:
            self.day.popleft()

    def _headers(self, now) -> dict:
        c = self.cfg
        used_m = c.tpm - self.avail
        reset_tok = used_m / (c.tpm / 60.0)
        reset_req = (self.day[0][0] + c.day_seconds - now) if self.day else 0.0
        return {"x-ratelimit-limit-requests": str(c.rpd), "x-ratelimit-limit-tokens": str(c.tpm),
                "x-ratelimit-remaining-requests": str(max(0, c.rpd - len(self.day))),
                "x-ratelimit-remaining-tokens": str(max(0, int(self.avail))),
                "x-ratelimit-reset-requests": f"{reset_req:.2f}s", "x-ratelimit-reset-tokens": f"{reset_tok:.2f}s"}

    def _limit_check(self, now, need: int):
        """Return (status, retry_after, message) if a limit refuses this request, else None."""
        c = self.cfg
        if need > c.tpm:
            return 413, 0.0, f"Request too large for model `standin-1` on tokens per minute (TPM): Limit {c.tpm}, Requested {need}."
        used_m = int(c.tpm - self.avail)
        if len(self.day) + 1 > c.rpd:
            ra = self.day[0][0] + c.day_seconds - now
            return 429, ra, f"Rate limit reached on requests per day (RPD): Limit {c.rpd}, Used {len(self.day)}, Requested 1."
        used_d = sum(t for _, t in self.day)
        if used_d + need > c.tpd:
            freed, ra = 0, c.day_seconds
            for t, tok in self.day:
                freed += tok
                if used_d - freed + need <= c.tpd:
                    ra = t + c.day_seconds - now
                    break
            return 429, ra, f"Rate limit reached on tokens per day (TPD): Limit {c.tpd}, Used {used_d}, Requested {need}."
        if len(self.minute) + 1 > c.rpm:
            ra = self.minute[len(self.minute) - c.rpm] + 60 - now
            return 429, ra, f"Rate limit reached on requests per minute (RPM): Limit {c.rpm}, Used {len(self.minute)}, Requested 1."
        if need > self.avail:
            ra = (need - self.avail) / (c.tpm / 60.0)
            return 429, ra, f"Rate limit reached on tokens per minute (TPM): Limit {c.tpm}, Used {used_m}, Requested {need}."
        if c.max_inflight and self.inflight >= c.max_inflight:
            return 429, 1.0, f"Too many concurrent requests: Limit {c.max_inflight}."
        return None

    @staticmethod
    def _reply_for(prompt: str) -> str:
        """Produce a reply in the pipeline's format by copying words from the first piece."""
        m = re.search(r"\[C1\][^\n]*\n(.+?)(?:\n\n\[C2\]|\Z)", prompt, re.S)
        if not m:
            return "ANSWERABLE: no\n"
        words = m.group(1).replace("|", " ").split()
        quote = " ".join(words[5:20]).replace('"', "")
        return f'ANSWERABLE: yes\nQ1 [C1] "{quote}"\nANSWER: According to the cited passage, {quote} [Q1].\n'

    async def chat(self, request: web.Request) -> web.StreamResponse:
        c = self.cfg
        body = await request.json()
        prompt = "\n".join(m.get("content", "") for m in body.get("messages", []))
        reply = self._reply_for(prompt)
        tokens_in = max(1, int(len(prompt) / c.chars_per_token))
        tokens_out = approx_tokens(reply) + c.extra_out_tokens
        now = time.monotonic()
        self._prune(now)
        self.stats["requests"] += 1
        refused = self._limit_check(now, tokens_in + tokens_out)
        headers = self._headers(now)
        if refused:
            status, ra, msg = refused
            self.stats[str(status)] += 1
            if status == 429:
                headers["retry-after"] = str(max(1, math.ceil(ra)))
                msg += f" Please try again in {ra:.2f}s."
            return web.json_response({"error": {"message": msg, "type": "tokens" if "token" in msg else "requests",
                                                "code": "rate_limit_exceeded"}}, status=status, headers=headers)
        if self.rng.random() < c.p_error:
            self.stats["503"] += 1
            return web.json_response({"error": {"message": "Service unavailable", "type": "server_error"}},
                                     status=503, headers=headers)
        self.minute.append(now)
        self.avail -= tokens_in + tokens_out
        self.day.append((now, tokens_in + tokens_out))
        headers = self._headers(now)
        self.inflight += 1
        self.stats["peak_inflight"] = max(self.stats["peak_inflight"], self.inflight)
        try:
            load = 1.0 + c.load_slowdown * (self.inflight - 1)
            ttft = (c.ttft_median_s + c.ttft_per_1k_in_s * tokens_in / 1000) * self.rng.lognormvariate(0.0, c.ttft_sigma) * load
            if self.rng.random() < c.p_slow:
                ttft *= c.slow_factor
            cut = self.rng.random() < c.p_cut
            await asyncio.sleep(max(0.0, self.frozen_until - time.monotonic()) + ttft)
            resp = web.StreamResponse(status=200, headers={**headers, "Content-Type": "text/event-stream"})
            await resp.prepare(request)
            pieces = re.findall(r"\S+\s*", reply)
            gap = load * 1.3 / c.tokens_per_s          # about 1.3 tokens per word
            for i, w in enumerate(pieces):
                if cut and i >= len(pieces) // 2:
                    self.stats["cut"] += 1
                    request.transport.close()          # abrupt: no finish marker
                    return resp
                chunk = {"choices": [{"index": 0, "delta": {"content": w}, "finish_reason": None}]}
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
                await asyncio.sleep(gap)
            usage = {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out}
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n".encode())
            await resp.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            self.stats["ok"] += 1
            return resp
        except (ConnectionResetError, asyncio.CancelledError):
            return web.Response(status=499)            # client went away; tokens stay charged
        finally:
            self.inflight -= 1

    async def admin_config(self, request: web.Request) -> web.Response:
        for k, v in (await request.json()).items():
            if k == "freeze_s":                     # stall: nothing is answered for this many seconds
                self.frozen_until = time.monotonic() + float(v)
            elif hasattr(self.cfg, k):
                setattr(self.cfg, k, type(getattr(self.cfg, k))(v))
        return web.json_response(asdict(self.cfg))

    async def admin_stats(self, request: web.Request) -> web.Response:
        now = time.monotonic()
        self._prune(now)
        return web.json_response({**self.stats, "inflight": self.inflight,
                                  "minute_tokens": int(self.cfg.tpm - self.avail),
                                  "day_tokens": sum(t for _, t in self.day), "day_requests": len(self.day)})

    async def admin_reset(self, request: web.Request) -> web.Response:
        self.minute.clear()
        self.avail, self.avail_t = float(self.cfg.tpm), time.monotonic()
        self.day.clear()
        self.stats = {k: 0 for k in self.stats}
        return web.json_response({"ok": True})

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_post("/admin/config", self.admin_config)
        app.router.add_get("/admin/stats", self.admin_stats)
        app.router.add_post("/admin/reset", self.admin_reset)
        return app


async def start(cfg: StandinConfig, port: int = 8089) -> tuple[Standin, web.AppRunner]:
    s = Standin(cfg)
    runner = web.AppRunner(s.app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return s, runner


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--config", help="JSON file with StandinConfig fields")
    a = ap.parse_args()
    cfg = StandinConfig(**json.load(open(a.config))) if a.config else StandinConfig()
    web.run_app(Standin(cfg).app(), host="127.0.0.1", port=a.port, access_log=None, print=lambda *_: print(f"stand-in on :{a.port}"))
