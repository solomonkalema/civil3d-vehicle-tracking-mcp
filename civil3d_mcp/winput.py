"""Talk to Civil 3D through its window, not through COM.

Why this exists: while Vehicle Tracking (or any command) waits at a command-line
prompt such as "select object to Follow", Civil 3D rejects every COM call, so a
pick point sent with SendCommand never arrives (Hoima job, 18 Oct 2026: every
Follow run needed Solomon to click the guide line by hand). The same happens
while PAN or another live command is running.

AutoCAD accepts typed input through the WM_COPYDATA window message: text sent
this way goes into the command line exactly as if it were typed, whether or not
COM is blocked. Escape is sent the same way (and as a key message as a backup).

Nothing here calls COM.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes

try:
    import win32gui

    HAVE_WIN32 = True
except ImportError:  # lets tests run off Windows
    HAVE_WIN32 = False

try:
    from pywinauto import Desktop  # type: ignore

    HAVE_UIA = HAVE_WIN32
except Exception:
    HAVE_UIA = False

WM_COPYDATA = 0x004A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_ESCAPE = 0x1B
SMTO_ABORTIFHUNG = 0x0002

CMDLINE_MARK = "Autodesk.AutoCAD.Windows.Data.CommandHistory"


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_void_p), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]


def send_text(main_hwnd: int, text: str, timeout_ms: int = 5000) -> bool:
    """Type `text` into Civil 3D's command line. End it with '\\n' (or a space) to press Enter.

    Returns True when the window accepted the message. Never sends an empty string:
    Enter on an empty prompt repeats the last command.
    """
    if not HAVE_WIN32:
        raise RuntimeError("Window input needs pywin32 on Windows.")
    if not text.strip() and "\x1b" not in text:
        raise ValueError("Empty input refused: Enter on an empty prompt repeats the last command.")
    buf = ctypes.create_unicode_buffer(text)
    cds = COPYDATASTRUCT()
    cds.dwData = 1
    cds.cbData = ctypes.sizeof(buf)
    cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
    res = wintypes.DWORD(0)
    ok = ctypes.windll.user32.SendMessageTimeoutW(
        wintypes.HWND(main_hwnd), WM_COPYDATA, 0, ctypes.byref(cds), SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(res)
    )
    return bool(ok)


def escape(main_hwnd: int, times: int = 2) -> None:
    """Cancel whatever is running at the command line (PAN, a prompt, a half-typed command)."""
    if not HAVE_WIN32:
        return
    try:
        send_text(main_hwnd, "\x1b" * times)
    except Exception:
        pass
    for _ in range(times):  # key messages as a backup route
        win32gui.PostMessage(main_hwnd, WM_KEYDOWN, VK_ESCAPE, 0x00010001)
        win32gui.PostMessage(main_hwnd, WM_KEYUP, VK_ESCAPE, 0xC0010001)
        time.sleep(0.1)


def _cmdline_windows(pid: int) -> list[int]:
    from .dialogs import top_windows

    return top_windows(pid)


def is_command_line(snapshot_window: dict) -> bool:
    """The floating command line looks like a window to the dialog watcher; it is not a dialog."""
    for c in snapshot_window.get("controls", []):
        if (c.get("name") or "") == CMDLINE_MARK:
            return True
    return False


def command_line_text(main_hwnd: int, pid: int, max_lines: int = 6) -> list[str]:
    """Last lines of the command line (history plus the live prompt), read through UI Automation.

    Best effort: returns [] if the command line cannot be found. Works while COM is blocked.
    """
    global _last_read, _last_lines
    if not HAVE_UIA:
        return []
    now = time.time()
    if now - _last_read < 0.8:  # UIA walks are slow; never more than about once a second
        return _last_lines
    _last_read = now
    lines: list[str] = []
    # Floating command line first (a small top-level window), the docked one (inside the main window) last:
    # walking the main window's ribbon is slow, so its hwnd is cached once found.
    others = [h for h in _cmdline_windows(pid) if h != main_hwnd]
    cands = ([_cmd_hwnd[pid]] if _cmd_hwnd.get(pid) else []) + others + [main_hwnd]
    seen: set[int] = set()
    for h in cands:
        if h in seen:
            continue
        seen.add(h)
        try:
            if not win32gui.IsWindow(h):
                continue
            win = Desktop(backend="uia").window(handle=h).wrapper_object()
            found = False
            texts: list[str] = []
            for c in win.descendants():
                ei = c.element_info
                name = (ei.name or "").strip()
                if name == CMDLINE_MARK:
                    found = True
                    continue
                if found and ei.control_type in ("Text", "Edit", "Document") and name:
                    texts.append(name)
            if found:
                _cmd_hwnd[pid] = h
                lines = texts[-max_lines:]
                break
        except Exception:
            continue
    _last_lines = lines
    return lines


_cmd_hwnd: dict[int, int] = {}
_last_read = 0.0
_last_lines: list[str] = []


PROMPT_PATTERNS = [
    r"select object to follow",
    r"select (an )?object",
    r"specify .*point",
    r"<select",
]


def looks_like_prompt(lines: list[str], patterns: list[str] | None = None) -> str | None:
    """The last prompt line if it matches one of the patterns, else None."""
    pats = [re.compile(p, re.I) for p in (patterns or PROMPT_PATTERNS)]
    for line in reversed(lines[-3:]):
        if any(p.search(line) for p in pats):
            return line
    return None
