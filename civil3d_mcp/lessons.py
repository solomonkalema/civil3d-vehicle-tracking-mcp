"""A lessons inbox: one file that every session writes to when it hits a gap or learns something (v0.10, 14 Nov 2026).

Lessons used to depend on someone writing them up at the end of a job. Now any session records one line at the moment it
happens: a tool that failed, a step that still needed the screen, a workaround, a client rule learnt, an idea for a tool.
The engineer (or a later session) reviews the inbox, folds what holds into the lessons and knowledge files, and ticks it.

The inbox is PRIVATE. It may name clients, people and folders, so it lives in a '.local' file that is never published.
Its place: the CIVIL3D_LESSONS_INBOX environment variable, else the path written in 'lessons_inbox_path.local.txt' beside
the knowledge file, else 'lessons_inbox.local.md' there.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

from .catalogue import HOME
from .com_bridge import Civil3DError

KINDS = ("gap", "bug", "lesson", "rule", "idea")
HEADER = ("# Lessons inbox (private: never publish)\n\n"
          "One line per entry, newest last. `[ ]` open, `[x]` folded into the lessons / knowledge / skill files or dismissed.\n"
          "Kinds: gap (the server could not do it), bug (a tool misbehaved), lesson (how something works), rule (a client or\n"
          "engineer's standing instruction), idea (a tool or check worth building).\n\n")
_LINE = re.compile(r"^- \[( |x)\] (\d{4}-\d{2}-\d{2}) \| ([^|]*) \| ([^|]*) \| (.*)$")


def inbox_path() -> Path:
    p = os.environ.get("CIVIL3D_LESSONS_INBOX")
    if p:
        return Path(p)
    pointer = HOME / "lessons_inbox_path.local.txt"  # one line: where the inbox is kept (e.g. a synced folder)
    if pointer.exists():
        q = pointer.read_text(encoding="utf-8-sig").strip().strip('"')
        if q:
            return Path(q)
    return HOME / "lessons_inbox.local.md"


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


def note(text: str, kind: str = "lesson", area: str = "general", context: str = "", today: str | None = None) -> dict:
    text = _one_line(text)
    if len(text) < 12:
        raise Civil3DError("Say what happened and what to do about it, in one or two sentences.")
    kind = kind.lower().strip()
    if kind not in KINDS:
        raise Civil3DError(f"kind is one of {KINDS}.")
    p = inbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.exists() else HEADER
    for ln in old.splitlines():
        m = _LINE.match(ln)
        if m and m.group(5).split(" || ")[0].strip().lower() == text.lower():
            return {"inbox": str(p), "added": False, "reason": "the same entry is already there", "entry": ln}
    line = f"- [ ] {today or _dt.date.today().isoformat()} | {kind} | {_one_line(area) or 'general'} | {text}"
    if _one_line(context):
        line += f" || {_one_line(context)}"
    p.write_text(old.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    return {"inbox": str(p), "added": True, "entry": line, "open": sum(1 for e in entries() if not e["done"])}


def entries() -> list[dict]:
    p = inbox_path()
    if not p.exists():
        return []
    out = []
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        m = _LINE.match(ln)
        if m:
            body = m.group(5).split(" || ")
            out.append({"n": i, "done": m.group(1) == "x", "date": m.group(2), "kind": m.group(3).strip(), "area": m.group(4).strip(),
                        "text": body[0].strip(), "context": " || ".join(body[1:]).strip()})
    return out


def read(open_only: bool = True, kind: str | None = None, area: str | None = None, limit: int = 40) -> dict:
    es = entries()
    sel = [e for e in es if (not open_only or not e["done"]) and (not kind or e["kind"] == kind.lower())
           and (not area or area.lower() in e["area"].lower())]
    return {"inbox": str(inbox_path()), "total": len(es), "open": sum(1 for e in es if not e["done"]),
            "by_kind_open": {k: sum(1 for e in es if e["kind"] == k and not e["done"]) for k in KINDS},
            "entries": sel[-limit:], "truncated": len(sel) > limit}


def done(match: str, outcome: str = "") -> dict:
    """Tick every open entry whose text contains `match`, adding where it went ('lessons 10.3', 'tool road_x', 'dismissed')."""
    if len(match.strip()) < 4:
        raise Civil3DError("Give a few words of the entry to close.")
    p = inbox_path()
    if not p.exists():
        return {"closed": 0, "note": "The inbox is empty."}
    lines, closed = p.read_text(encoding="utf-8").splitlines(), []
    for i, ln in enumerate(lines):
        m = _LINE.match(ln)
        if m and m.group(1) == " " and match.lower() in ln.lower():
            lines[i] = ln.replace("- [ ]", "- [x]", 1) + (f" || done: {_one_line(outcome)}" if outcome else "")
            closed.append(lines[i])
    if closed:
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"closed": len(closed), "entries": closed[:10]}
