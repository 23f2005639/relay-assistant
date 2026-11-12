"""Every tunable number lives here, so the write-up can point at one file.

Numbers marked GUESS have not been measured yet; the probe run replaces them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = ROOT / "papers" / "library"
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"

# Read KEY=value lines from .env (never committed, never logged). Real environment variables win.
if (ROOT / ".env").exists():
    for _line in (ROOT / ".env").read_text().splitlines():
        _k, _sep, _v = _line.strip().partition("=")
        if _sep and _k and not _k.startswith("#"):
            os.environ.setdefault(_k.removeprefix("export ").strip(), _v.strip().strip("'\""))

PAPERS = {
    "ATT": {
        "file": "attention_1706.03762v7.pdf",
        "title": "Attention Is All You Need",
        "arxiv": "1706.03762v7",
        # Words that point at this paper. Used by the router, never shown to the model.
        "aliases": ["attention is all you need", "vaswani", "bleu", "wmt", "english-to-german",
                    "english-to-french", "multi-head", "scaled dot-product", "positional encoding",
                    "label smoothing", "newstest", "constituency parsing", "original transformer"],
    },
    "MOSHI": {
        "file": "moshi_2410.00037v1.pdf",
        "title": "Moshi: a speech-text foundation model for real-time dialogue",
        "arxiv": "2410.00037v1",
        "aliases": ["moshi", "mimi", "helium", "inner monologue", "full-duplex", "speech-to-speech",
                    "codec", "rq-transformer", "kyutai", "spoken dialogue", "real-time dialogue",
                    "acoustic token", "semantic token", "audio token", "streaming asr", "tts"],
    },
    "HNET": {
        "file": "hnet_2507.07955v2.pdf",
        "title": "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        "arxiv": "2507.07955v2",
        "aliases": ["h-net", "hnet", "dynamic chunking", "byte-level", "bits-per-byte", "mamba",
                    "routing module", "smoothing module", "ratio loss", "tokenizer-free", "bpe",
                    "hg38", "dna", "hellaswag", "boundary predictor", "hierarchical sequence"],
    },
}


@dataclass(frozen=True)
class RetrievalSetting:
    name: str
    piece_words: int      # target words per piece (about 1.3 tokens per word)
    k: int                # pieces sent to the model
    rerank: bool          # coverage re-ranking over a wider candidate pool
    pool: int = 20        # candidates considered when re-ranking
    dense: bool = False   # fuse keyword search with a small local embedding model (measured: it hurt, see relay/recall.py)
    route_first_at: float = 0.75   # a paper with this share of the routing score is listed first
    route_boost: float = 1.0   # how strongly the routed paper is preferred (0 = router off)


RETRIEVAL_SETTINGS = {
    "small": RetrievalSetting("small-150w-k8-norerank", piece_words=150, k=8, rerank=False),
    "medium": RetrievalSetting("medium-300w-k4-rerank", piece_words=300, k=4, rerank=True),
    "large": RetrievalSetting("large-600w-k2-norerank", piece_words=600, k=2, rerank=False),
}
DEFAULT_SETTING = "medium"


@dataclass
class Limits:
    """What we believe the provider allows. Headers and 429s overwrite these at run time."""
    rpm: int = 30          # docs, free tier, gpt-oss-20b
    tpm: int = 8000        # docs
    rpd: int = 1000        # docs
    tpd: int = 200_000     # docs; NOT visible in headers, tracked by our own ledger
    day_seconds: float = 86_400.0


@dataclass
class Policy:
    patience_s: float = 20.0        # GUESS: a waiting person gives up after this with nothing on screen
    hard_cap_s: float = 60.0        # nobody waits longer than this for the whole reply
    lean_below_frac: float = 0.35   # minute budget left below this fraction -> lean level
    day_reserve_frac: float = 0.02  # stop calling the model when this little of the day is left
    max_attempts: int = 2           # one retry per model call at most
    retry_bucket_cap: float = 10.0  # Brooker's retry bucket
    retry_deposit: float = 0.1      # each success adds this much
    throttle_k: float = 2.0         # Google SRE adaptive throttling multiplier
    throttle_window_s: float = 30.0   # 120 s kept refusing for two minutes after a 15 s outage at this traffic
    call_timeout_s: float = 30.0    # whole model call
    first_byte_timeout_s: float = 10.0
    out_tokens_full: int = 450      # measured: 350 cut answers off mid-sentence (thinking tokens count as output)
    out_tokens_lean: int = 260      # measured: 180 cut 3 of 12 lean answers off
    lean_k: int = 2
    refuse_below: float = 0.34      # GUESS: evidence score under this -> refuse without a model call
    retry_search_below: float = 0.55  # GUESS: under this -> rewrite the query and search once more
    coalesce: bool = True           # identical in-flight questions share one model call
    tool_timeout_s: float = 2.0
    naive: bool = False             # experiment only: no ledger, no retry budget, retry at once (the thing NOT to do)
    force_full: bool = False        # quality runs: always wait for a full answer, never degrade


@dataclass
class Provider:
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "openai/gpt-oss-20b"
    api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""), repr=False)   # never printed
    source: str = "real"            # "real" or "stand-in", copied into results.csv
    reasoning_effort: str | None = "low"

    @staticmethod
    def standin(port: int = 8089) -> "Provider":
        return Provider(base_url=f"http://127.0.0.1:{port}/v1", model="standin-1",
                        api_key="standin", source="stand-in", reasoning_effort=None)


def approx_tokens(text: str) -> int:
    """Cheap token estimate. Replaced by the provider's own usage numbers after each call."""
    return max(1, int(len(text) / 3.6))


# USD per million tokens, from console.groq.com/docs/model/openai/gpt-oss-20b (read 2026-11-11).
# The free tier is not billed; this is what the same traffic would cost on the paid tier.
PRICE_IN_PER_M = 0.075
PRICE_OUT_PER_M = 0.30
