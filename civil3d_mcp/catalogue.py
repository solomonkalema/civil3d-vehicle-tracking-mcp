"""Dialog catalogue: what the server has learned about Civil 3D pop-ups.

Every dialog the server meets is recorded here. A dialog is only ever answered
without Claude when ALL of these hold:

  * its catalogue entry is confirmed (Solomon or Claude confirmed it once, live),
  * its control fingerprint matches one recorded for that entry exactly,
  * its message text still contains the entry's key phrase,
  * the running task supplied an answer for that entry (or the entry has a
    confirmed fixed answer), and any `when_text_matches` condition holds.

Anything else (a new dialog, a changed one, a judgement call without a policy)
comes back to Claude in full. Nothing is clicked past unseen.

The file is plain JSON so it can be read and edited by hand.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("CIVIL3D_MCP_HOME", Path(__file__).resolve().parent.parent / "knowledge"))
CATALOGUE_FILE = HOME / "dialog_catalogue.json"
SEED_FILE = Path(__file__).resolve().parent / "dialog_catalogue_seed.json"

# Controls whose names are stable labels (part of the fingerprint).
_LABEL_TYPES = {"Button", "CheckBox", "RadioButton", "Text", "Group", "TabItem", "Hyperlink", "MenuItem"}
# Controls whose presence counts but whose content varies.
_SHAPE_TYPES = {"ComboBox", "Edit", "Tree", "List", "DataGrid", "Table", "Spinner", "Slider", "Tab", "Pane"}

_lock = threading.Lock()


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("&", "")).strip()


def full_text(snapshot: dict) -> str:
    """All readable text of a dialog group, in order, for phrase matching."""
    parts: list[str] = []
    for w in snapshot.get("windows", []):
        parts.append(w.get("title", ""))
        parts.extend(w.get("text", []))
        for c in w.get("controls", []):
            if c.get("name"):
                parts.append(c["name"])
    return norm(" ".join(parts)).lower()


def fingerprint(snapshot: dict) -> str:
    """Stable signature of a dialog group: titles, window classes and the set of
    labelled controls. Long text, numbers and values are left out on purpose:
    they change between runs (vehicle names, dimensions) without the dialog
    being a different one. Those are checked separately with the text phrase.
    """
    sig: list[Any] = []
    for w in sorted(snapshot.get("windows", []), key=lambda w: (w.get("title", ""), w.get("class", ""))):
        items = []
        for c in w.get("controls", []):
            t = c.get("type", "")
            name = norm(c.get("name"))
            if t in _LABEL_TYPES and name and len(name) <= 40 and not re.search(r"\d", name):
                items.append(f"{t}:{name.lower()}")
            elif t in _SHAPE_TYPES:
                items.append(t)
        sig.append([norm(w.get("title")).lower(), w.get("class", ""), sorted(set(items))])
    return hashlib.sha1(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:12]


class Catalogue:
    def __init__(self, path: Path = CATALOGUE_FILE) -> None:
        self.path = Path(path)
        self.data = self._load()

    # ---------------------------------------------------------------- storage
    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        if SEED_FILE.exists():
            data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        else:
            data = {"version": 1, "entries": [], "workflows": {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def save(self) -> None:
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    @property
    def entries(self) -> list[dict]:
        return self.data.setdefault("entries", [])

    def get(self, entry_id: str) -> dict | None:
        return next((e for e in self.entries if e["id"] == entry_id), None)

    # ---------------------------------------------------------------- matching
    def match(self, snapshot: dict) -> dict:
        """Return {"status": exact|similar|unknown, "entry": entry or None, "fingerprint": fp, "reason": str}."""
        m = self._match(snapshot)
        m["_text"] = full_text(snapshot)
        return m

    def _match(self, snapshot: dict) -> dict:
        fp = fingerprint(snapshot)
        text = full_text(snapshot)
        titles = {norm(w.get("title")).lower() for w in snapshot.get("windows", [])}

        def phrase_ok(e: dict) -> bool:
            p = norm(e.get("text_contains")).lower()
            return not p or p in text

        def title_ok(e: dict) -> bool:
            t = norm(e.get("title")).lower()
            return not t or t in titles

        exact = [e for e in self.entries if fp in e.get("fingerprints", []) and phrase_ok(e) and title_ok(e)]
        if exact:
            # several entries may share buttons (two Yes/No message boxes); the
            # longest matching phrase is the most specific
            e = max(exact, key=lambda e: len(e.get("text_contains") or ""))
            status = "exact" if e.get("confirmed") else "similar"
            return {"status": status, "entry": e, "fingerprint": fp,
                    "reason": "fingerprint and phrase match" + ("" if e.get("confirmed") else ", entry not yet confirmed")}
        similar = [e for e in self.entries if title_ok(e) and e.get("title") and phrase_ok(e) and e.get("text_contains")]
        if not similar:
            similar = [e for e in self.entries if e.get("title") and title_ok(e) and not e.get("text_contains")]
        if similar:
            e = max(similar, key=lambda e: len(e.get("text_contains") or ""))
            why = "title and phrase match, but the controls differ from every recorded version" if e.get("fingerprints") else \
                "title and phrase match a catalogue entry that has never been confirmed live"
            return {"status": "similar", "entry": e, "fingerprint": fp, "reason": why}
        return {"status": "unknown", "entry": None, "fingerprint": fp, "reason": "not in the catalogue"}

    def observe(self, snapshot: dict, m: dict) -> None:
        """Count every sighting; keep unknown dialogs on file so they are not lost."""
        e = m.get("entry")
        if e is None:
            uid = f"unknown.{m['fingerprint']}"
            e = self.get(uid)
            if e is None:
                titles = [w.get("title", "") for w in snapshot.get("windows", [])]
                e = {
                    "id": uid, "title": titles[0] if titles else "", "text_contains": "",
                    "description": "Seen but not yet explained. Confirm with c3d_dialog_act (learn=...).",
                    "workflows": [], "kind": "unknown", "confirmed": False, "fingerprints": [m["fingerprint"]],
                    "example": compact(snapshot), "seen": 0, "first_seen": _now(),
                }
                self.entries.append(e)
        e["seen"] = int(e.get("seen", 0)) + 1
        e["last_seen"] = _now()
        e.setdefault("first_seen", e["last_seen"])
        self.save()

    def learn(
        self,
        snapshot: dict,
        entry_id: str,
        *,
        title: str | None = None,
        text_contains: str | None = None,
        description: str | None = None,
        kind: str | None = None,
        question: str | None = None,
        workflows: list[str] | None = None,
        fixed_actions: list[dict] | None = None,
        notes: str | None = None,
        confirmed: bool = True,
    ) -> dict:
        """Record (or confirm) what a dialog is and how it is answered."""
        fp = fingerprint(snapshot)
        e = self.get(entry_id)
        if e is None:
            e = {"id": entry_id, "fingerprints": [], "seen": 1, "first_seen": _now()}
            self.entries.append(e)
        # an 'unknown.<fp>' placeholder is replaced by the named entry
        self.data["entries"] = [x for x in self.entries if not (x["id"].startswith("unknown.") and fp in x.get("fingerprints", []) and x is not e)]
        if fp not in e.setdefault("fingerprints", []):
            e["fingerprints"].append(fp)
        titles = [w.get("title", "") for w in snapshot.get("windows", [])]
        e["title"] = title if title is not None else e.get("title") or (titles[0] if titles else "")
        for k, v in (("text_contains", text_contains), ("description", description), ("kind", kind), ("question", question),
                     ("workflows", workflows), ("fixed_actions", fixed_actions), ("notes", notes)):
            if v is not None:
                e[k] = v
        e["example"] = compact(snapshot)
        e["confirmed"] = bool(confirmed)
        e["confirmed_on"] = _now() if confirmed else None
        self.save()
        return e

    # ---------------------------------------------------------------- pre-flight
    def preflight(self, workflow: str) -> dict:
        wf = self.data.get("workflows", {}).get(workflow, {})
        rows = []
        for e in self.entries:
            if workflow not in e.get("workflows", []):
                continue
            rows.append({
                "id": e["id"],
                "dialog": e.get("title"),
                "what": e.get("description"),
                "kind": e.get("kind"),
                "confirmed_live": bool(e.get("confirmed")),
                "question": e.get("question"),
                "answer_format": e.get("answer_format"),
            })
        return {
            "workflow": workflow,
            "ask_before_running": wf.get("questions", []),
            "known_dialogs": rows,
            "how_answers_are_used": (
                "Pass answers to the run tool keyed by dialog id. A dialog is answered without Claude only on a "
                "confirmed exact match; unconfirmed or changed dialogs still come back for a decision."
            ),
        }

    def summary(self) -> list[dict]:
        return [
            {"id": e["id"], "title": e.get("title"), "kind": e.get("kind"), "confirmed": bool(e.get("confirmed")),
             "versions": len(e.get("fingerprints", [])), "seen": e.get("seen", 0), "last_seen": e.get("last_seen")}
            for e in self.entries
        ]


# ---------------------------------------------------------------- answers
def resolve_answer(m: dict, answers: dict | None) -> tuple[list[dict] | None, str]:
    """Decide whether the server may answer this dialog itself.

    Returns (actions, reason). actions is None when Claude must decide.
    """
    e = m.get("entry")
    if m.get("status") != "exact" or e is None:
        return None, m.get("reason", "not an exact confirmed match")
    spec = (answers or {}).get(e["id"])
    if spec is None and e.get("kind") != "judgement" and e.get("fixed_actions"):
        spec = {"actions": e["fixed_actions"]}
    if spec is None:
        if e.get("kind") == "judgement":
            return None, "judgement dialog and the task gave no policy for it"
        return None, "known dialog, but the task supplied no answer for it"
    if isinstance(spec, list):
        spec = {"actions": spec}
    cond = spec.get("when_text_matches")
    if cond and not re.search(cond, m.get("_text", ""), re.I):
        return None, f"the task's condition '{cond}' does not hold for this dialog's text"
    return list(spec.get("actions", [])), "confirmed exact match with a task-supplied answer"


# ---------------------------------------------------------------- display
def compact(snapshot: dict, max_controls: int = 120) -> dict:
    """Snapshot trimmed for Claude: ids, types, labels, values. No rectangles."""
    out = {"windows": []}
    for w in snapshot.get("windows", []):
        cw = {"title": w.get("title"), "active": w.get("active")}
        if w.get("text"):
            cw["text"] = w["text"]
        ctrls = []
        for c in w.get("controls", [])[:max_controls]:
            r = {k: c[k] for k in ("id", "type", "name", "label", "value", "options", "checked", "selected", "expanded", "enabled", "depth") if k in c and c[k] not in (None, "", [])}
            if r.get("enabled") is True:
                r.pop("enabled")
            ctrls.append(r)
        if len(w.get("controls", [])) > max_controls:
            ctrls.append({"note": f"{len(w['controls']) - max_controls} more controls not shown"})
        cw["controls"] = ctrls
        if w.get("unreadable"):
            cw["unreadable"] = w["unreadable"]
        out["windows"].append(cw)
    return out


def deepcopy(x):
    return copy.deepcopy(x)
