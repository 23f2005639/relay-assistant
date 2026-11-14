"""Copy the raw Claude Code transcripts for this project here and list my own prompts.

  python conversation_history/export.py      # run again just before submitting, to pick up the latest turns

The .jsonl files are the unedited record (every prompt, reply and tool call). prompts.md is only an index.
"""
import json
import shutil
from pathlib import Path

src = Path.home() / ".claude/projects/-home-human-itnern-proj"
here = Path(__file__).parent
prompts = []
for f in sorted(src.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
    shutil.copy(f, here / f.name)
    for line in f.open():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
            continue
        c = e.get("message", {}).get("content")
        texts = [c] if isinstance(c, str) else [x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") == "text"]
        for t in texts:
            t = t.strip()
            # skip what the tool itself inserts: reminders, command echoes, background-task notices, summaries
            if t and not t.startswith(("<", "[SYSTEM", "This session is being continued", "Caveat:", "Base directory for this skill")):
                prompts.append((e.get("timestamp", "")[:16].replace("T", " "), t))
prompts = sorted(dict.fromkeys(prompts))      # a resumed session repeats earlier turns
out = ["# My prompts, in order (verbatim)\n", "The full conversation is in the .jsonl files next to this one.\n"]
out += [f"\n## {i}. {ts} UTC\n\n{t}\n" for i, (ts, t) in enumerate(prompts, 1)]
(here / "prompts.md").write_text("\n".join(out))
print(len(prompts), "prompts;", len(list(here.glob("*.jsonl"))), "transcript files copied")
