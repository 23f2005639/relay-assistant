"""Check the model's reply while it streams. No model calls.

The model must write quotes first, then the answer:

    ANSWERABLE: yes
    Q1 [C2] "exact words copied from piece C2"
    ANSWER: One sentence [Q1]. Another sentence [Q1].

A quote is released only if it really occurs in the cited piece.
A sentence is released only if it cites a verified quote and every number in it appears in its quotes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

QUOTE_LINE = re.compile(r"^\s*Q(\d+)\s*\[C(\d+)\]\s*[\"“”'](.+?)[\"“”']?\s*$")
CITE = re.compile(r"\[Q(\d+)\]")
NUM = re.compile(r"\d+(?:[.,]\d+)*")
# A sentence ends at ". " before a capital. A citation written after the full stop ("... scores. [Q1]")
# stays with its sentence; that is how the real model often writes it (seen in runs/answers-medium-real.jsonl).
SENT_END = re.compile(r"(?<=[.!?\]])(?<!et al\.)(?<!e\.g\.)(?<!i\.e\.)(?<!vs\.)\s+(?=[A-Z])")


def norm(s: str) -> str:
    s = s.lower().replace("-\n", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    s = re.sub(r"[“”\"'’‘`]", "", s)
    s = re.sub(r"[\s|]+", " ", s)
    return s.strip(" .,;:")


def quote_in(quote: str, text: str, min_ratio: float = 0.9) -> bool:
    q, t = norm(quote), norm(text)
    if len(q.split()) < 3:
        return False                      # one or two words prove nothing
    if q in t:
        return True
    qw, tw = q.split(), t.split()
    blocks = SequenceMatcher(None, qw, tw, autojunk=False).get_matching_blocks()
    big = [b for b in blocks if b.size >= 3]   # only runs of 3+ words count, so scattered words do not
    if not big:
        return False
    span = big[-1].b + big[-1].size - big[0].b
    return sum(b.size for b in big) / len(qw) >= min_ratio and span <= len(qw) * 1.3


# Names that say which paper a sentence is about. "Transformer" is deliberately absent: all three papers use it.
PAPER_NAMES = {"MOSHI": ("moshi", "mimi", "helium"), "HNET": ("h-net", "hnet"),
               "ATT": ("attention is all you need", "vaswani")}


def papers_named(s: str) -> set[str]:
    low = re.sub(r"[\u2010-\u2015]", "-", s.lower())      # the model writes H‑Net with a non-breaking hyphen
    return {p for p, names in PAPER_NAMES.items() if any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in names)}


def numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(CITE.sub("", s))}


@dataclass
class Event:
    kind: str          # refuse | quote | bad_quote | sentence | dropped
    text: str = ""
    pieces: list[int] = field(default_factory=list)   # context indexes (1-based) backing this
    note: str = ""


class StreamChecker:
    def __init__(self, question: str, context: list[str], papers: list[str] | None = None):
        self.question = question
        self.context = context                 # context[i-1] is the text of [Ci]
        self.papers = papers                   # papers[i-1] is the paper [Ci] comes from, if known
        self.wrong_paper = 0
        self.buf = ""
        self.in_answer = False
        self.quotes: dict[int, tuple[int, str]] = {}   # qid -> (piece index, quote text)
        self.refused = False
        self.answerable_seen = False
        self.sentences_ok = 0
        self.sentences_dropped = 0
        self.bad_quotes = 0

    def feed(self, text: str) -> list[Event]:
        self.buf += text
        return self._drain(final=False)

    def finish(self) -> list[Event]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[Event]:
        out: list[Event] = []
        while True:
            if not self.in_answer:
                if self.buf.lstrip().lower().startswith("answer:"):
                    # do not wait for the end of the line: sentences must be released as they arrive
                    self.in_answer = True
                    self.buf = self.buf.lstrip()[len("answer:"):].lstrip()
                    continue
                line, sep, rest = self.buf.partition("\n")
                if not sep and not final:
                    break
                self.buf = rest
                out += self._header_line(line)
                if not sep and not self.in_answer:
                    break
            else:
                parts = SENT_END.split(self.buf, maxsplit=1)
                if len(parts) == 2:
                    self.buf = parts[1]
                    out += self._sentence(parts[0])
                elif final and self.buf.strip():
                    s, self.buf = self.buf, ""
                    out += self._sentence(s)
                else:
                    break
        return out

    def _header_line(self, line: str) -> list[Event]:
        s = line.strip()
        if not s:
            return []
        low = s.lower()
        if low.startswith("answerable:"):
            self.answerable_seen = True
            if low.split(":", 1)[1].strip().startswith("no"):     # "yes, but not all" is a yes
                self.refused = True
                return [Event("refuse")]
            return []
        if low.startswith("answer:"):
            self.in_answer = True
            self.buf = s.split(":", 1)[1].lstrip() + ("\n" + self.buf if self.buf else "")
            return []
        m = QUOTE_LINE.match(s)
        if m:
            qid, cid, quote = int(m.group(1)), int(m.group(2)), m.group(3)
            if 1 <= cid <= len(self.context) and quote_in(quote, self.context[cid - 1]):
                self.quotes[qid] = (cid, quote)
                return [Event("quote", quote, [cid])]
            self.bad_quotes += 1
            return [Event("bad_quote", quote, [cid] if 1 <= cid <= len(self.context) else [],
                          "quote not found in the cited piece")]
        return []

    def _sentence(self, sent: str) -> list[Event]:
        sent = sent.strip()
        if not sent:
            return []
        cited = [int(q) for q in CITE.findall(sent)]
        good = [q for q in cited if q in self.quotes]
        nums = numbers(sent)
        if good:
            backing = " ".join(self.quotes[q][1] for q in good) + " " + self.question
            # a number is fine if the quote has it, or the cited piece has it right next to the quote
            pieces_text = " ".join(self.context[self.quotes[q][0] - 1] for q in good)
            missing = [n for n in nums if n not in numbers(backing) and n not in numbers(pieces_text)]
            # A sentence about Moshi must be backed by a Moshi passage. Seen on the real model: an H-Net
            # statement attributed to Moshi with a genuine H-Net quote (runs/answers-small-real.jsonl, q19).
            named = papers_named(sent)
            cited = {self.papers[self.quotes[q][0] - 1] for q in good} if self.papers else named
            if not named <= cited:
                self.wrong_paper += 1
                self.sentences_dropped += 1
                return [Event("dropped", sent, [], f"names {sorted(named - cited)} but cites only {sorted(cited)}")]
            if not missing:
                self.sentences_ok += 1
                return [Event("sentence", sent, sorted({self.quotes[q][0] for q in good}))]
            note = f"number(s) {missing} not in the cited source"
        else:
            note = "no verified quote cited"
        self.sentences_dropped += 1
        return [Event("dropped", sent, [], note)]
