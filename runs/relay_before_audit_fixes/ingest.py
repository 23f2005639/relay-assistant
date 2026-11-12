"""Turn the three PDFs into citable pieces.

Each piece knows its paper, page and section, so a citation can say where it came from.
Reference lists are dropped: they are a third of the text and match searches for the wrong reason.
Tables get an extra layout-preserving piece, because plain extraction separates numbers from row labels.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import DATA_DIR, LIBRARY_DIR, PAPERS

NUM_HEAD = re.compile(r"^(\d{1,2})((?:\.\d{1,2}){0,2})$")
LET_HEAD = re.compile(r"^([A-Z])((?:\.\d{1,2}){0,2})$")
INLINE_HEAD = re.compile(r"^((?:\d{1,2}|[A-Z])(?:\.\d{1,2}){0,2})\s+([A-Z][A-Za-z].{2,70})$")
APPENDIX_HEAD = re.compile(r"^Appendix ([A-Z])\.\s+(.{3,80})$")
TABLE_CAP = re.compile(r"^\s*Table (\d+)[:.]")
RUNNING_HEADERS = {"Moshi: a speech-text foundation model for real-time dialogue",
                   "Défossez, Mazaré, Orsini, Royer, Pérez, Jégou, Grave and Zeghidour"}


@dataclass
class Piece:
    id: str
    paper: str
    page: int
    section: str
    kind: str        # body | appendix | table
    text: str

    def header(self) -> str:
        p = PAPERS[self.paper]
        sec = f" | {self.section}" if self.section else ""
        return f"{p['title']} ({p['arxiv']}){sec} | p.{self.page}"


def _pdftotext(path: Path, layout: bool = False) -> list[str]:
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + [str(path), "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return out.split("\f")


def _titleish(line: str) -> bool:
    w = line.split()
    return 0 < len(w) <= 10 and line[0].isupper() and not line.rstrip().endswith((".", ",", ";"))


def _key(num: str) -> tuple:
    return tuple(int(x) if x.isdigit() else ord(x) - 64 for x in num.split("."))


def _lines_with_sections(pages: list[str]):
    """Yield (page, section, kind, line) for every kept line; skips the reference list."""
    section, kind = "", "body"
    last_num: tuple = (0,)
    last_let: tuple = (0,)
    in_refs = False
    for pi, page in enumerate(pages, start=1):
        lines = [l.strip() for l in page.split("\n") if l.strip()]
        i = 0
        while i < len(lines):
            line = lines[i]
            if line in RUNNING_HEADERS:
                i += 1
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            head = None
            m = INLINE_HEAD.match(line)
            ma = APPENDIX_HEAD.match(line)
            if ma:
                head, step = (ma.group(1), ma.group(2)), 1
            elif m and _titleish(m.group(2)):
                head, step = (m.group(1), m.group(2)), 1
            elif (NUM_HEAD.match(line) or LET_HEAD.match(line)) and _titleish(nxt):
                head, step = (line, nxt), 2
            if head:
                num, title = head
                is_letter = num[0].isalpha()
                k = _key(num)
                ok = False
                if is_letter and (in_refs or kind == "appendix"):
                    ok = k > last_let and k[0] <= last_let[0] + 1
                    if ok:
                        last_let, kind, in_refs = k, "appendix", False
                elif not is_letter and not in_refs and kind == "body":
                    ok = k > last_num and 1 <= k[0] <= last_num[0] + 1 and (len(k) == 1 or k[0] == last_num[0])
                    if ok:
                        last_num = k
                if ok:
                    section = f"§{num} {title}"
                    i += step
                    continue
            if line == "References" and kind == "body":
                in_refs = True
                i += 1
                continue
            if in_refs and line == "Attention Visualizations":
                in_refs, kind, section = False, "appendix", "Attention Visualizations"
            if not in_refs and line:
                yield pi, section, kind, line
            i += 1


def _table_pieces(paper: str, path: Path) -> list[Piece]:
    out = []
    for pi, page in enumerate(_pdftotext(path, layout=True), start=1):
        lines = page.split("\n")
        for i, line in enumerate(lines):
            m = TABLE_CAP.match(line)
            if not m:
                continue
            lo, hi = max(0, i - 30), min(len(lines), i + 34)
            block = "\n".join(re.sub(r" {3,}", "  |  ", l.rstrip()) for l in lines[lo:hi] if l.strip())
            out.append(Piece(f"{paper}-p{pi}-t{m.group(1)}", paper, pi, f"Table {m.group(1)}", "table", block))
    return out


def build_pieces(piece_words: int, overlap: float = 0.15) -> list[Piece]:
    pieces: list[Piece] = []
    for paper, meta in PAPERS.items():
        path = LIBRARY_DIR / meta["file"]
        buf: list[str] = []
        cur = None  # (page, section, kind) of the first line in the buffer
        n = 0

        def flush(keep_tail: bool):
            nonlocal buf, cur, n
            if buf and cur:
                n += 1
                pieces.append(Piece(f"{paper}-p{cur[0]}-c{n}", paper, cur[0], cur[1], cur[2], " ".join(buf)))
            if keep_tail and buf:
                words = " ".join(buf).split()
                buf = [" ".join(words[-int(piece_words * overlap):])]
            else:
                buf = []

        for page, section, kind, line in _lines_with_sections(_pdftotext(path)):
            if cur and (section != cur[1] or kind != cur[2]):
                flush(keep_tail=False)
                cur = None
            if cur is None:
                cur = (page, section, kind)
            buf.append(line)
            if sum(len(b.split()) for b in buf) >= piece_words:
                flush(keep_tail=True)
                cur = (page, section, kind)
        flush(keep_tail=False)
        pieces.extend(_table_pieces(paper, path))
    return pieces


def load_pieces(piece_words: int) -> list[Piece]:
    """Build once per piece size and cache on disk."""
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"pieces_{piece_words}w.json"
    if cache.exists():
        return [Piece(**d) for d in json.loads(cache.read_text())]
    pieces = build_pieces(piece_words)
    cache.write_text(json.dumps([asdict(p) for p in pieces]))
    return pieces


if __name__ == "__main__":
    import collections
    import sys
    words = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    ps = build_pieces(words)
    print(len(ps), "pieces")
    print(collections.Counter((p.paper, p.kind) for p in ps))
    for paper in PAPERS:
        secs = []
        for p in ps:
            if p.paper == paper and p.section and p.section not in secs:
                secs.append(p.section)
        print(paper, len(secs), "sections:", secs[:12])
