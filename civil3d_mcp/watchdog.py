"""Blocker watchdog (v0.7, 26 Oct 2026): find, read, picture and (where safe) clear whatever stops Civil 3D.

Why: on 26 Oct a 'Drawing Recovery' notice sat over Civil 3D for minutes. It is a WebView2 window, so neither
the computer-use screenshots nor acad.exe's own window list showed it, every COM call hung, and only the person at the PC
could see it. The server runs on the PC and can see every window, so it has to be the eyes when no one is there.

What it does
* blockers(): every visible top-level window of acad.exe AND its child processes (WebView2 helpers draw some
  Civil 3D 2027 dialogs) that could be holding Civil 3D: anything while the main window is disabled (a modal is
  up), plus known notices and standard #32770 dialogs at any time.
* classify(): one rule set for every pop-up -
    safe_notice  a known notice that only informs (catalogue below): closed automatically, any time.
    notice       only an OK button (or just the window's X), no inputs, no risky words: closed and logged.
    cancelable   has Cancel and no risky words: Cancel is pressed and logged (the step stops, nothing changes).
    progress     a progress window such as 'Plot Progress' (v0.9): left alone, since Cancel would abort the work.
    protected    mentions saving, overwriting, deleting, licences, sign-in...: NEVER answered here.
    unknown      anything else: never answered here.
  Protected and unknown are reported in full (text, buttons, picture) for Claude to take to the person.
* A sweeper thread closes safe_notice windows every few seconds, all the time, so a notice can't stall work.
* screen(): picture of the Civil 3D window area as it is on the monitor (dialogs included, other apps cropped
  away), for when something looks wrong and the person is away.

Every action is logged to knowledge/run_logs/watchdog.jsonl with a picture of the dialog taken before acting.
Nothing here uses COM: COM is exactly what a blocker stops.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import json
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from . import dialogs as dlg
from .catalogue import HOME

LOG_DIR = HOME / "run_logs"
LOG_FILE = LOG_DIR / "watchdog.jsonl"
SWEEP_EVERY_S = 3.0

# Known notices that only inform. Title must match exactly. Add entries here as they are met and judged safe.
SAFE_NOTICES: dict[str, str] = {
    "Drawing Recovery": "Crash notice; the backups stay listed in the Drawing Recovery Manager palette.",
}

# Words that make a pop-up the person's decision, whatever its buttons say.
PROTECTED = re.compile(
    r"\bsave|overwrit|replac|discard|delet|eras|purg|\blos[et]|licen[cs]|sign[ -]?in|log[ -]?in|subscri|trial|"
    r"activat|password|account|install|updat|upgrad|restor|recover|reload|xref|read[- ]only|locked|in use|"
    r"another user|exit|quit|close (the )?(drawing|program|application)",
    re.I,
)
INPUT_TYPES = {"Edit", "ComboBox", "RadioButton", "List", "Tree", "Spinner", "Slider"}
OK_NAMES = {"ok", "okay"}
CANCEL_NAMES = {"cancel"}
_lock = threading.Lock()


# ------------------------------------------------------------------------------------------------ processes
class _PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


def processes() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, exe name), from a Toolhelp snapshot (no extra packages)."""
    out: dict[int, tuple[int, str]] = {}
    try:
        k32 = ctypes.windll.kernel32
    except AttributeError:
        return out
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return out
    try:
        e = _PE()
        e.dwSize = ctypes.sizeof(_PE)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.decode("mbcs", "replace").lower())
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def civil_pids(procs: dict[int, tuple[int, str]] | None = None) -> dict[int, str]:
    """acad.exe processes plus everything they started (WebView2 helpers and their children)."""
    procs = processes() if procs is None else procs
    roots = {p for p, (_, n) in procs.items() if n == "acad.exe"}
    keep = {p: procs[p][1] for p in roots}
    changed = True
    while changed:
        changed = False
        for p, (pp, n) in procs.items():
            if p not in keep and pp in keep and pp != p:
                keep[p] = n
                changed = True
    return keep


# ------------------------------------------------------------------------------------------------ windows
def main_window(pids: dict[int, str]) -> int:
    """The Civil 3D main frame: the largest visible acad.exe window titled 'Autodesk Civil 3D ...'."""
    if not dlg.HAVE_WIN32:
        return 0
    best, area = 0, 0
    for pid, name in pids.items():
        if name != "acad.exe":
            continue
        for h in dlg.top_windows(pid):
            t = dlg.win32gui.GetWindowText(h)
            if t.startswith("Autodesk"):
                l, tp, r, b = dlg.win32gui.GetWindowRect(h)
                if (r - l) * (b - tp) > area:
                    best, area = h, (r - l) * (b - tp)
    return best


def blockers(main_hwnd: int = 0) -> dict:
    """Windows that may be holding Civil 3D. Cheap: titles and handles only, no UI Automation."""
    if not dlg.HAVE_WIN32:
        return {"modal": False, "main_hwnd": 0, "windows": []}
    pids = civil_pids()
    main = main_hwnd if main_hwnd and dlg.win32gui.IsWindow(main_hwnd) else main_window(pids)
    modal = bool(main) and not dlg.win32gui.IsWindowEnabled(main)
    wins = []
    for pid, name in pids.items():
        for h in dlg.top_windows(pid):
            if h == main or not dlg._is_dialog_like(h):
                continue
            try:
                title = dlg.win32gui.GetWindowText(h)
                cls = dlg.win32gui.GetClassName(h)
                enabled = bool(dlg.win32gui.IsWindowEnabled(h))
            except Exception:  # closed while being listed
                continue
            if title in SAFE_NOTICES or (modal and enabled) or (cls == "#32770" and enabled):
                wins.append({"hwnd": h, "title": title, "class": cls, "process": name, "pid": pid,
                             "rect": list(dlg.win32gui.GetWindowRect(h))})
    return {"modal": modal, "main_hwnd": main, "windows": wins}


def read(hwnd: int) -> dict:
    """Full contents of one window (UI Automation, win32 fallback)."""
    return dlg.snapshot([hwnd])["windows"][0]


def classify(w: dict) -> tuple[str, str | None]:
    """(class, button to press or 'WM_CLOSE' or None)."""
    title = w.get("title") or ""
    if title in SAFE_NOTICES:
        return "safe_notice", _ok_or_close(w)
    if dlg.is_progress(w):
        return "progress", None  # work in progress (Plot Progress): Cancel would abort it, so leave it alone
    ctrls = w.get("controls", [])
    words = " ".join([title] + list(w.get("text", [])) + [str(c.get("name") or "") for c in ctrls])
    if PROTECTED.search(words):
        return "protected", None
    buttons = {str(c.get("name") or "").strip().lower(): c for c in ctrls if c.get("type") == "Button" and c.get("name")}
    inputs = [c for c in ctrls if c.get("type") in INPUT_TYPES]
    if any(b in CANCEL_NAMES for b in buttons):
        return "cancelable", next(c["name"] for k, c in buttons.items() if k in CANCEL_NAMES)
    if not inputs and set(buttons) <= OK_NAMES:
        # A plain notice. Floating palettes can also have no inputs and no buttons, so outside the catalogue only
        # a standard Windows dialog (#32770) or a window with an OK button counts; anything else stays 'unknown'.
        # ('Close' buttons are dropped by the reader, which can't tell them from the title-bar X; WM_CLOSE is the
        # same as pressing them.)
        if w.get("class") == "#32770" or buttons:
            return "notice", _ok_or_close(w)
    return "unknown", None


def _ok_or_close(w: dict) -> str:
    for c in w.get("controls", []):
        if c.get("type") == "Button" and str(c.get("name") or "").strip().lower() in OK_NAMES:
            return c["name"]
    return "WM_CLOSE"


def press(w: dict, button: str) -> str:
    if button == "WM_CLOSE":
        dlg.win32gui.PostMessage(w["hwnd"], dlg.win32con.WM_CLOSE, 0, 0)
        return "closed the window (same as its Close / X)"
    return "; ".join(dlg.perform({"windows": [w]}, [{"click": button}]))


def _log(event: str, **data: Any) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        row = {"t": _dt.datetime.now().isoformat(timespec="seconds"), "event": event, **data}
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _picture(hwnd: int, tag: str) -> str | None:
    try:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return str(dlg.capture(hwnd, LOG_DIR / "watchdog" / f"{stamp}-{tag}.png"))
    except Exception:
        return None


def handle(policy: str = "auto", main_hwnd: int = 0, skip: set[int] | None = None) -> dict:
    """Read every blocker and act within the policy.

    policy 'none'  read and report only
           'safe'  close known notices and plain OK notices
           'auto'  also press Cancel where offered (the step stops, nothing changes)
    Protected and unknown pop-ups are never answered here.
    """
    with _lock:
        b = blockers(main_hwnd)
        report = []
        for meta in b["windows"]:
            if skip and meta["hwnd"] in skip:
                continue
            if not dlg.win32gui.IsWindow(meta["hwnd"]):
                continue  # closed since it was listed
            try:
                w = read(meta["hwnd"])
            except Exception as e:
                w = {"hwnd": meta["hwnd"], "title": meta["title"], "controls": [], "text": [],
                     "unreadable": [f"{type(e).__name__}: {e}"]}
            kind, button = classify(w)
            item = {"title": w.get("title") or meta["title"], "process": meta["process"], "class": kind,
                    "text": w.get("text", [])[:20],
                    "buttons": [c.get("name") for c in w.get("controls", []) if c.get("type") == "Button"][:15],
                    "inputs": [{k: c.get(k) for k in ("type", "label", "name", "value", "checked") if c.get(k) is not None}
                               for c in w.get("controls", []) if c.get("type") in INPUT_TYPES | {"CheckBox"}][:15],
                    "hwnd": meta["hwnd"]}
            allowed = {"none": set(), "safe": {"safe_notice", "notice"},
                       "auto": {"safe_notice", "notice", "cancelable"}}.get(policy, set())
            if kind in allowed and button:
                item["picture_before"] = _picture(meta["hwnd"], "before")
                try:
                    item["action"] = press(w, button)
                except Exception as e:
                    item["action_error"] = f"{type(e).__name__}: {e}"
                _log("handled", **{k: v for k, v in item.items() if k != "hwnd"}, policy=policy)
            elif kind in ("protected", "unknown"):
                item["picture"] = _picture(meta["hwnd"], "needs-person")
                item["needs"] = ("A decision for the person. Quote this dialog to them; if they may be away, stop the "
                                 "task here and send them a notification. Answer later with c3d_blockers(click=..., "
                                 "hwnd=...).")
                _log("needs_person", **{k: v for k, v in item.items() if k != "hwnd"})
            report.append(item)
        return {"modal": b["modal"], "blockers": report}


def click(hwnd: int, button: str, reason: str) -> dict:
    """Press one named button (or 'WM_CLOSE') on one window, on Claude's or the person's decision."""
    w = read(hwnd)
    pic = _picture(hwnd, "before")
    done = press(w, button)
    _log("clicked", title=w.get("title"), button=button, reason=reason, picture_before=pic)
    return {"title": w.get("title"), "action": done, "picture_before": pic}


def sweep_once() -> list[str]:
    """Close known safe notices only. Cheap enough to run every few seconds."""
    if not dlg.HAVE_WIN32:
        return []
    closed = []
    for meta in blockers()["windows"]:
        if meta["title"] in SAFE_NOTICES:
            pic = _picture(meta["hwnd"], "sweeper")
            try:
                dlg.win32gui.PostMessage(meta["hwnd"], dlg.win32con.WM_CLOSE, 0, 0)
                closed.append(meta["title"])
                _log("sweeper_closed", title=meta["title"], why=SAFE_NOTICES[meta["title"]], picture_before=pic)
            except Exception:
                pass
    return closed


def start_sweeper() -> threading.Thread | None:
    if not dlg.HAVE_WIN32:
        return None

    def loop():
        while True:
            try:
                sweep_once()
            except Exception:
                pass
            time.sleep(SWEEP_EVERY_S)

    t = threading.Thread(target=loop, daemon=True, name="civil3d-sweeper")
    t.start()
    return t


# ------------------------------------------------------------------------------------------------ pictures
def screen(out: Path, max_width: int = 1600) -> dict:
    """The Civil 3D window area as it is on the monitor, dialogs included, other apps cropped away."""
    from PIL import ImageGrab

    u32 = ctypes.windll.user32
    try:  # read window rects and grab the screen in the same (physical-pixel) coordinates
        u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass
    b = blockers()
    if not b["main_hwnd"]:
        raise RuntimeError("No Civil 3D window is open.")
    rects = [list(dlg.win32gui.GetWindowRect(b["main_hwnd"]))] + [list(dlg.win32gui.GetWindowRect(w["hwnd"]))
                                                                  for w in b["windows"]]
    l = min(r[0] for r in rects); t = min(r[1] for r in rects)
    r_ = max(r[2] for r in rects); bt = max(r[3] for r in rects)
    vx, vy = u32.GetSystemMetrics(76), u32.GetSystemMetrics(77)  # virtual-screen origin
    img = ImageGrab.grab(all_screens=True)
    img = img.crop((l - vx, t - vy, r_ - vx, bt - vy))
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return {"path": str(out), "modal": b["modal"], "blockers": [w["title"] for w in b["windows"]]}
