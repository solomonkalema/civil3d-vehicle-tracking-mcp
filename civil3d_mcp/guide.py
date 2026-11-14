"""The server's own guidance, served by topic and section (v0.10, 14 Nov 2026).

c3d_knowledge returns the whole knowledge file (long). c3d_guide returns one topic, or one section of it, so a session
reads what the job needs: the lessons, the drawing-review checklist, the tool map, or a numbered knowledge section.
Everything served from here is generic and published; private notes stay in the '.local' files.
"""
from __future__ import annotations

import re
from pathlib import Path

from .catalogue import HOME
from .com_bridge import Civil3DError

TOPICS = {
    "lessons": ("lessons_road_drawing_production.md",
                "What was learnt on real jobs: order of work, rules, sheet checks, how to review a set, what a working "
                "drawing must carry, what the server can and cannot reach."),
    "review_checklist": ("review_checklist.md",
                         "The drawing-review checklist, in order, with what a working drawing carries that a design "
                         "drawing does not, and the order of production."),
    "tool_map": ("tool_map.md", "Which tool for which job, and what still needs the screen."),
    "knowledge": ("knowledge.md", "The full knowledge base by numbered section (Vehicle Tracking, dialogs, road production)."),
}
_HEAD = re.compile(r"^## +(.*)$", re.M)
DOCS = Path(__file__).resolve().parent.parent / "knowledge"  # the published guidance ships with the code


def _file(name: str) -> Path:
    return HOME / name if (HOME / name).exists() else DOCS / name


def _sections(text: str) -> list[tuple[str, str]]:
    ms = list(_HEAD.finditer(text))
    out = [("(introduction)", text[:ms[0].start()].strip())] if ms and text[:ms[0].start()].strip() else []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((m.group(1).strip(), text[m.start():end].strip()))
    return out or [("(all)", text.strip())]


def guide(topic: str = "index", section: str | None = None, max_chars: int = 14000) -> dict:
    topic = (topic or "index").strip().lower()
    if topic == "index":
        rows = []
        for k, (fname, what) in TOPICS.items():
            f = _file(fname)
            secs = [t for t, _ in _sections(f.read_text(encoding="utf-8-sig"))] if f.exists() else []
            rows.append({"topic": k, "what": what, "sections": secs, "present": f.exists()})
        return {"topics": rows,
                "how": "c3d_guide('<topic>') for a short topic, c3d_guide('<topic>', '<number or words of a heading>') for one section."}
    if topic not in TOPICS:
        raise Civil3DError(f"Topics: index, {', '.join(TOPICS)}.")
    f = _file(TOPICS[topic][0])
    if not f.exists():
        raise Civil3DError(f"{f.name} is missing beside the knowledge file.")
    text = f.read_text(encoding="utf-8-sig")
    secs = _sections(text)
    if section is None:
        if len(text) <= max_chars:
            return {"topic": topic, "text": text}
        return {"topic": topic, "too_long": f"{len(text)} characters. Ask for one section.", "sections": [t for t, _ in secs]}
    want = str(section).strip().lower()
    hits = [(t, b) for t, b in secs if re.match(rf"{re.escape(want)}[\.\s:]", t.lower() + " ")] if re.fullmatch(r"[\d\.]+", want) else []
    hits = hits or [(t, b) for t, b in secs if want in t.lower()]
    if not hits:
        raise Civil3DError(f"No section matching '{section}'. Sections: {[t for t, _ in secs]}")
    body = "\n\n".join(b for _, b in hits[:3])
    out = {"topic": topic, "sections": [t for t, _ in hits[:3]], "text": body[:max_chars]}
    if len(body) > max_chars:
        out["truncated"] = f"{len(body)} characters: raise max_chars or ask for a narrower section."
    return out
