"""Local search over the pieces. No model calls, no tokens.

route()    : which paper is the question about?
search()   : BM25 over piece text with the paper/section header prepended, fused (reciprocal rank)
             with a small local embedding model when the setting asks for it. Still no model-API tokens.
evidence   : a 0..1 score saying how well the best pieces cover the question's rare words
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .config import DATA_DIR, PAPERS, RetrievalSetting
from .ingest import Piece, load_pieces

WORD = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
STOP = set("""a an the of in on for to and or is are was were be been do does did what which who whom how why when
where that this these those it its as at by with from about into than then so such can could would should may might
will shall have has had not no yes paper papers authors author say says said state states used use using their they
them there here also any all each per between during according model models""".split())


def _stem(t: str) -> str:
    """Plural-only stemming, so 'boundaries' meets 'boundary'. Anything more aggressive hurts numbers and names."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in WORD.findall(text.lower()) if t not in STOP and len(t) > 2]


@dataclass
class Hit:
    piece: Piece
    score: float


@dataclass
class Retrieved:
    hits: list[Hit]
    evidence: float            # 0..1, coverage of rare question words by the chosen pieces
    route: dict[str, float]    # paper -> share


class Index:
    def __init__(self, setting: RetrievalSetting):
        self.setting = setting
        self.pieces = load_pieces(setting.piece_words)
        self.docs = [Counter(tokenize(p.header() + " " + p.text)) for p in self.pieces]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.max_idf = max(self.idf.values())
        # how concentrated each word is in one paper: "transformer" is everywhere, "bleu" is not
        per_paper = {k: Counter() for k in PAPERS}
        for p, d in zip(self.pieces, self.docs):
            per_paper[p.paper].update(d.keys())
        self.paper_df = per_paper
        self.vecs = None
        if setting.dense:
            self._load_vectors()

    # ---- local embeddings (bge-small, 384 numbers per piece, runs on CPU) ----
    _embedder = None

    @classmethod
    def _model(cls):
        if cls._embedder is None:
            from fastembed import TextEmbedding
            cls._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        return cls._embedder

    def _load_vectors(self):
        import numpy as np
        path = DATA_DIR / f"vecs_{self.setting.piece_words}w.npy"
        if path.exists():
            v = np.load(path)
            if len(v) == len(self.pieces):
                self.vecs = v
                return
        v = np.array(list(self._model().embed([p.header() + "\n" + p.text for p in self.pieces])), dtype="float32")
        self.vecs = v / np.linalg.norm(v, axis=1, keepdims=True)
        np.save(path, self.vecs)

    def _dense_order(self, query: str) -> list[int]:
        import numpy as np
        q = np.array(next(iter(self._model().query_embed(query))), dtype="float32")
        sims = self.vecs @ (q / np.linalg.norm(q))
        return [int(i) for i in np.argsort(-sims)]

    # ---- step 1: route -------------------------------------------------
    def route(self, question: str) -> dict[str, float]:
        q = question.lower()
        scores = {}
        for paper, meta in PAPERS.items():
            s = sum(3.0 for a in meta["aliases"] if a in q)
            for t in set(tokenize(question)):
                tot = sum(self.paper_df[k][t] for k in PAPERS)
                if tot:
                    share = self.paper_df[paper][t] / tot
                    if share > 0.8:               # word lives almost only in this paper
                        s += self.idf.get(t, 0) / self.max_idf
            scores[paper] = s
        total = sum(scores.values())
        if total == 0:
            return {k: 0.0 for k in PAPERS}
        return {k: v / total for k, v in scores.items()}

    # ---- step 2: search ------------------------------------------------
    def _bm25(self, q_terms: list[str], k1: float = 1.4, b: float = 0.7) -> list[float]:
        out = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in q_terms:
                f = d.get(t, 0)
                if f:
                    s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / self.avg))
            out.append(s)
        return out

    def _ranked(self, query: str, route: dict[str, float]) -> tuple[list[int], list[float]]:
        """All pieces with a non-zero score, best first."""
        scores = self._bm25(tokenize(query))
        # soft boost, not a filter: a wrong route must not hide the right paper completely
        scores = [s * (1.0 + self.setting.route_boost * route.get(p.paper, 0.0)) for s, p in zip(scores, self.pieces)]
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        order = [i for i in order if scores[i] > 0]
        if self.vecs is not None:
            # reciprocal-rank fusion: a piece ranked high by either the words or the meaning comes first
            fused: dict[int, float] = {}
            for r, i in enumerate(order[:50]):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + r)
            for r, i in enumerate(self._dense_order(query)[:50]):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + r)
            order = sorted(fused, key=lambda i: -fused[i])
            scores = [fused.get(i, 0.0) for i in range(len(self.pieces))]
        # A paper named outright goes first. Other papers stay in the list, behind it.
        top = max(route, key=route.get) if route and max(route.values()) >= self.setting.route_first_at else None
        if top:
            order = [i for i in order if self.pieces[i].paper == top] + [i for i in order if self.pieces[i].paper != top]
        return order, scores

    def search(self, query: str, k: int | None = None, route: dict[str, float] | None = None) -> Retrieved:
        k = k or self.setting.k
        route = route if route is not None else self.route(query)
        q_terms = tokenize(query)
        order, scores = self._ranked(query, route)
        if self.setting.rerank:
            chosen = self._coverage_rerank(q_terms, order[: self.setting.pool], k)
        else:
            chosen = order[:k]
        hits = [Hit(self.pieces[i], scores[i]) for i in chosen]
        return Retrieved(hits, self._evidence(q_terms, chosen), route)

    def _coverage_rerank(self, q_terms: list[str], cand: list[int], k: int) -> list[int]:
        """Greedy pick by marginal coverage of question words (idea from PACE, without embeddings).
        Favours pieces that add words the already-chosen pieces lack, so fewer pieces cover more."""
        qs = set(q_terms)
        covered: set[str] = set()
        chosen: list[int] = []
        rank = {i: r for r, i in enumerate(cand)}
        if cand:                        # the best-ranked piece always stays first (the lean level sends only two)
            chosen.append(cand[0])
            covered |= qs & set(self.docs[cand[0]])
        while cand and len(chosen) < k:
            def gain(i):
                new = (qs & set(self.docs[i])) - covered
                return (sum(self.idf.get(t, 0) for t in new), -rank[i])
            best = max((i for i in cand if i not in chosen), key=gain, default=None)
            if best is None:
                break
            chosen.append(best)
            covered |= qs & set(self.docs[best])
        return chosen

    def _evidence(self, q_terms: list[str], chosen: list[int]) -> float:
        qs = set(q_terms)
        if not qs or not chosen:
            return 0.0
        present: set[str] = set()
        for i in chosen:
            present |= qs & set(self.docs[i])
        # words absent from the whole library count against the evidence (they often mean the papers
        # do not cover the question), but only at a middling weight: many are just ordinary verbs
        w = lambda t: self.idf.get(t, self.max_idf * 0.5)
        return sum(w(t) for t in present) / sum(w(t) for t in qs)

    # ---- step 3 helper: one rewrite for the retry loop ------------------
    def rewrite(self, question: str, route: dict[str, float]) -> str:
        """Second attempt: keep only the rare words and add the routed paper's title."""
        terms = sorted(set(tokenize(question)), key=lambda t: -self.idf.get(t, self.max_idf))
        keep = terms[: max(3, len(terms) // 2 + 1)]
        best = max(route, key=route.get) if any(route.values()) else None
        return " ".join(keep) + (" " + PAPERS[best]["title"] if best else "")


_INDEXES: dict[str, Index] = {}


def get_index(setting: RetrievalSetting) -> Index:
    if setting.name not in _INDEXES:
        _INDEXES[setting.name] = Index(setting)
    return _INDEXES[setting.name]
