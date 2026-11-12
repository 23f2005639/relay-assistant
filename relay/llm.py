"""One streaming chat call to an OpenAI-compatible endpoint (Groq or the stand-in).

Yields text as it arrives and reports timings, token usage and rate-limit headers.
Never retries: retry decisions belong to one layer only (control.py), so retries cannot multiply.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from .config import Provider


class RateLimited(Exception):
    def __init__(self, retry_after: float, kind: str, body: str, headers: dict):
        super().__init__(f"429 {kind} retry_after={retry_after}")
        self.retry_after, self.kind, self.body, self.headers = retry_after, kind, body, headers


class TooLarge(Exception):
    """413: this single request is bigger than the per-minute allowance. Retrying cannot help."""


class ServerError(Exception):
    pass


class CallTimeout(Exception):
    pass


class StreamCut(Exception):
    """The stream ended without a finish marker."""


@dataclass
class CallStats:
    t_send: float = 0.0
    t_first_byte: float | None = None
    t_first_token: float | None = None
    t_end: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    headers: dict = field(default_factory=dict)
    status: int = 0


_DUR = re.compile(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?")


def parse_duration(s: str | None) -> float | None:
    """Groq writes resets like '2m59.56s' or '7.66s' or '120ms'."""
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = _DUR.fullmatch(s.strip())
        if not m or not any(m.groups()):
            return None
        h, mi, sec, ms = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(sec or 0) + float(ms or 0) / 1000


def limit_kind(body: str) -> str:
    """Which limit was hit, read from the error text: minute/day x requests/tokens."""
    b = body.lower()
    for tag, kind in (("(rpm)", "requests_per_minute"), ("(tpm)", "tokens_per_minute"),
                      ("(rpd)", "requests_per_day"), ("(tpd)", "tokens_per_day")):
        if tag in b:          # Groq names the limit outright; "Need more tokens?" appears in every message
            return kind
    unit = "tokens" if "token" in b else "requests"
    window = "day" if ("per day" in b or "tpd" in b or "rpd" in b) else "minute"
    return f"{unit}_per_{window}"


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(limits=httpx.Limits(max_connections=500, max_keepalive_connections=100))
    return _client


async def stream_chat(provider: Provider, messages: list[dict], max_tokens: int, stats: CallStats,
                      first_byte_timeout: float = 10.0, total_timeout: float = 30.0) -> AsyncIterator[str]:
    body = {"model": provider.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0,
            "stream": True, "stream_options": {"include_usage": True}}
    if provider.reasoning_effort and "gpt-oss" in provider.model:
        body["reasoning_effort"] = provider.reasoning_effort
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    stats.t_send = time.monotonic()
    deadline = stats.t_send + total_timeout
    timeout = httpx.Timeout(connect=5.0, read=first_byte_timeout, write=5.0, pool=5.0)
    finished = False
    try:
        async with client().stream("POST", provider.base_url + "/chat/completions", json=body,
                                   headers=headers, timeout=timeout) as resp:
            stats.t_first_byte = time.monotonic()
            stats.status = resp.status_code
            stats.headers = {k.lower(): v for k, v in resp.headers.items()
                             if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"}
            if resp.status_code >= 400:
                text = (await resp.aread()).decode(errors="replace")
                text = re.sub(r"org_[0-9a-z]{16,}", "org_REDACTED", text)   # keep account ids out of logs
                if resp.status_code == 429:
                    ra = parse_duration(resp.headers.get("retry-after")) or 1.0
                    raise RateLimited(ra, limit_kind(text), text, stats.headers)
                if resp.status_code == 413:
                    raise TooLarge(text)
                if resp.status_code >= 500:
                    raise ServerError(f"{resp.status_code}")
                raise RuntimeError(f"{resp.status_code}: {text[:300]}")     # 401, 400, 404: retrying cannot help
            async for line in resp.aiter_lines():
                if time.monotonic() > deadline:
                    raise CallTimeout("total")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    finished = True
                    break
                ev = json.loads(data)
                usage = ev.get("usage") or (ev.get("x_groq") or {}).get("usage")
                if usage:
                    stats.tokens_in = usage.get("prompt_tokens", 0)
                    stats.tokens_out = usage.get("completion_tokens", 0)
                    stats.cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                    stats.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                for ch in ev.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        if stats.t_first_token is None:
                            stats.t_first_token = time.monotonic()
                        yield piece
                    if ch.get("finish_reason"):
                        finished = True
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise CallTimeout(type(e).__name__) from e
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, ValueError) as e:   # ValueError: a line cut mid-JSON
        raise StreamCut(type(e).__name__) from e
    finally:
        stats.t_end = time.monotonic()
    if not finished:
        raise StreamCut("no finish marker")
