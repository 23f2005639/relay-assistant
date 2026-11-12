"""Free retrieval check: for each question, do the pieces sent to the model contain the facts needed?

  python -m relay.recall

Uses the "must" strings in questions.json (written by hand from the papers). Spends no tokens, so the
retrieval settings can be compared before any model call is made.
"""
from __future__ import annotations

import dataclasses
import re

from .config import RETRIEVAL_SETTINGS
from .loadgen import load_questions
from .retrieve import Index

_n = lambda s: re.sub(r"\s+", " ", s.lower())


def recall(setting, questions, k=None) -> tuple[float, list[str]]:
    ix = Index(setting)
    got, missed = 0.0, []
    for q in questions:
        r = ix.search(q["question"], k=k)
        text = _n(" ".join(h.piece.text for h in r.hits))
        found = sum(_n(m) in text for m in q["must"]) / len(q["must"])
        got += found
        if found < 1:
            missed.append(q["id"])
    return got / len(questions), missed


def main():
    qs = [q for q in load_questions() if q.get("must")]
    print(f"{len(qs)} answerable questions; share of needed facts present in the pieces sent to the model")
    for name, base in RETRIEVAL_SETTINGS.items():
        for label, kw in [("keywords only", dict(route_boost=0.0, route_first_at=9)),
                          ("+ router", dict()),
                          ("+ router + meaning", dict(dense=True))]:
            for rr in (False, True):
                s = dataclasses.replace(base, rerank=rr, **kw)
                r, missed = recall(s, qs)
                print(f"  {name:6s} {s.piece_words}w k={s.k} | {label:30s} | rerank={'on ' if rr else 'off'} | {r:.2f} | missed {' '.join(missed)}")


if __name__ == "__main__":
    main()
