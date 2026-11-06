"""Start Civil 3D when none is running (v0.6, 26 Oct 2026).

Civil 3D takes one to three minutes to start, longer than the 60 s a tool call may take. So the launch runs
in the background: c3d_launch starts it and returns, a watcher thread waits until Civil 3D answers COM and is
idle, and later calls (c3d_launch again, or c3d_status) report progress. If a folder and name were given,
the watcher then makes the fresh working drawing itself.

How Civil 3D is started
-----------------------
* The Civil 3D 2027 *Metric* Start-menu shortcut is found and opened through explorer.exe, which is exactly
  what a double click does. Explorer starts the program, so Civil 3D is not a child of this server and keeps
  running when the MCP server restarts (the desktop app can end a server's whole process tree).
* If explorer did not start it, the shortcut's own target and arguments are run directly, detached.
* With no shortcut: acad.exe with the Civil 3D Metric profile, built from the AutoCAD install folder.
* knowledge/launch.json can override all of this: {"shortcut": "...lnk"} or {"exe": "...", "args": "..."}.

Rules kept from earlier versions: never start a second instance. If acad.exe is already running (even still
loading), the launcher waits for it instead of starting another.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .catalogue import HOME

try:  # Windows only
    import pythoncom
    import pywintypes
    import win32com.client

    HAVE_COM = True
except ImportError:
    HAVE_COM = False

YEARS = ("2027", "2026", "2025")
PROGIDS = ("AutoCAD.Application.26", "AutoCAD.Application.25", "AutoCAD.Application.24", "AutoCAD.Application")
START_TIMEOUT_S = 420.0  # give up after 7 minutes (licence check, first-run set-up)
EXPLORER_GRACE_S = 25.0  # time for acad.exe to appear after explorer.exe opens the shortcut
SETTLE_S = 4.0  # Civil 3D must stay idle this long before it counts as ready
HIDDEN_S = 40.0  # an existing acad.exe with no window that has not answered COM by now is a leftover
SKIP_WORDS = ("uninstall", "reset", "migrat", "batch", "enabler", "subassembly", "grading", "help", "readme",
              "license", "licence", "what's new", "whats new", "import", "export")

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


# ---------------------------------------------------------------------- finding the launcher
def rank_shortcut(name: str) -> int | None:
    """Score a shortcut file name; None if it is not a Civil 3D launcher. Higher is better.

    Metric beats unmarked, unmarked beats Imperial; a newer year beats an older one.
    """
    n = name.lower()
    if not n.endswith(".lnk") or "civil 3d" not in n:
        return None
    if any(w in n for w in SKIP_WORDS):
        return None
    year = next((y for y in YEARS if y in n), None)
    if year is None:
        return None
    score = (len(YEARS) - YEARS.index(year)) * 100
    if "metric" in n:
        score += 20
    elif "imperial" in n:
        score -= 20
    return score


def shortcut_dirs() -> list[Path]:
    env = os.environ
    dirs = [
        Path(env.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(env.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(env.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
        Path(env.get("USERPROFILE", "")) / "Desktop",
    ]
    return [d for d in dirs if str(d) and d.is_dir()]


def find_shortcut() -> Path | None:
    best: tuple[int, Path] | None = None
    for d in shortcut_dirs():
        try:
            for p in d.rglob("*.lnk"):
                s = rank_shortcut(p.name)
                if s is not None and (best is None or s > best[0]):
                    best = (s, p)
        except OSError:
            continue
    return best[1] if best else None


def read_shortcut(path: Path) -> dict:
    sh = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(path))
    return {"target": sh.TargetPath, "args": sh.Arguments, "workdir": sh.WorkingDirectory}


def fallback_exe() -> dict | None:
    pf = Path(os.environ.get("ProgramW6432", os.environ.get("ProgramFiles", r"C:\Program Files")))
    for y in YEARS:
        base = pf / "Autodesk" / f"AutoCAD {y}"
        exe = base / "acad.exe"
        if exe.is_file():
            args = (f'/ld "{base / "AecBase.dbx"}" /p "<<C3D_Metric>>" /product "C3D" /language "en-US"')
            return {"target": str(exe), "args": args, "workdir": str(base)}
    return None


def launch_plan() -> dict:
    """What would be started, without starting it."""
    cfg_file = HOME / "launch.json"
    if cfg_file.is_file():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"{cfg_file} could not be read: {e}")
        if cfg.get("shortcut"):
            p = Path(cfg["shortcut"])
            return {"source": "launch.json shortcut", "shortcut": str(p), **read_shortcut(p)}
        if cfg.get("exe"):
            return {"source": "launch.json exe", "shortcut": None, "target": cfg["exe"], "args": cfg.get("args", ""),
                    "workdir": str(Path(cfg["exe"]).parent)}
    lnk = find_shortcut()
    if lnk is not None:
        return {"source": "Start menu shortcut", "shortcut": str(lnk), **read_shortcut(lnk)}
    exe = fallback_exe()
    if exe:
        return {"source": "AutoCAD install folder (no shortcut found)", "shortcut": None, **exe}
    raise RuntimeError("Could not find Civil 3D: no 'Civil 3D' shortcut in the Start menu or on the desktop, and no "
                       "AutoCAD 20xx\\acad.exe under Program Files. Put the shortcut path in knowledge/launch.json "
                       'as {"shortcut": "C:\\\\...\\\\Civil 3D 2027 - Metric.lnk"}.')


# ---------------------------------------------------------------------- process and window checks
def acad_pids() -> list[int]:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=10, creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().strip('"') == "acad.exe":
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def acad_windows(pids: list[int]) -> list[str]:
    """Titles of visible top-level windows of acad.exe: shows a licence or first-run dialog holding the start."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return []
    titles: list[str] = []

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                t = win32gui.GetWindowText(hwnd)
                if t:
                    titles.append(t)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return titles[:12]


def end_processes(pids: list[int]) -> list[str]:
    """End the given processes; returns taskkill's own messages (e.g. 'Access is denied' for an elevated acad.exe)."""
    msgs = []
    for pid in pids:
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=20,
                               creationflags=0x08000000)
            m = " ".join((r.stdout + " " + r.stderr).split())
        except Exception as e:
            m = f"{type(e).__name__}: {e}"
        msgs.append(f"pid {pid}: {m}"[:300])
    return msgs


NOTICE_SWEEP_S = 20.0  # notices can appear after Civil 3D first reports idle; keep closing them this long


def close_notices() -> list[str]:
    """Close known informational notices (watchdog.SAFE_NOTICES), e.g. the post-crash 'Drawing Recovery'."""
    from . import watchdog

    try:
        return watchdog.sweep_once()
    except Exception:
        return []


def get_running_app():
    """Running AutoCAD/Civil 3D on the calling thread (which must have called CoInitialize), or None."""
    for progid in PROGIDS:
        try:
            return win32com.client.GetActiveObject(progid)
        except pywintypes.com_error:
            continue
    return None


def is_idle(app) -> bool:
    try:
        return bool(app.GetAcadState().IsQuiescent)
    except Exception:
        try:  # older type info: a readable Documents collection is the next best sign
            app.Documents.Count
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------- the launcher
class Launcher:
    def __init__(self, c3d) -> None:
        self.c3d = c3d  # the Civil3D bridge: used to make the new drawing once Civil 3D is ready
        self.lock = threading.Lock()
        self.state = "idle"  # idle | starting | ready | blocked | failed
        self.started: float | None = None
        self.ready_at: float | None = None
        self.plan: dict | None = None
        self.method: str | None = None
        self.notes: list[str] = []
        self.request: dict | None = None  # {"folder", "name", "template"} for the fresh drawing
        self.drawing: dict | None = None  # result of making it
        self.hidden_pids: list[int] = []
        self._thread: threading.Thread | None = None

    # ---- public
    def start(self, folder: str | None, name: str | None, template: str | None, end_hidden: bool = False) -> dict:
        with self.lock:
            if self.state == "starting":
                if folder and name and not self.request:
                    self.request = {"folder": folder, "name": name, "template": template}
                return self.report()
            self.state, self.started, self.ready_at = "starting", time.time(), None
            self.notes, self.drawing, self.method, self.hidden_pids = [], None, None, []
            self.request = {"folder": folder, "name": name, "template": template} if folder and name else None
            pids = acad_pids()
            if pids and end_hidden:
                if acad_windows(pids):
                    self.state = "failed"
                    self._note("acad.exe has a window open, so it is a real Civil 3D and was not ended. "
                               "Use it, or close it yourself first.")
                    return self.report()
                msgs = end_processes(pids)
                self._note(f"asked Windows to end hidden acad.exe (pid {', '.join(map(str, pids))}): {'; '.join(msgs)}")
                end = time.time() + 15
                while pids and time.time() < end:
                    time.sleep(1.0)
                    pids = acad_pids()
                if pids:
                    self.state = "failed"
                    self._note(f"acad.exe (pid {', '.join(map(str, pids))}) would not end. 'Access is denied' means it runs "
                               "as administrator: end it in Task Manager (Details, acad.exe, End task). If even that "
                               "fails it is stuck inside Windows, often on a network or cloud-drive file: restart the PC.")
                    return self.report()
            if pids:
                self.method = "waiting for the Civil 3D that is already starting (no second instance)"
                self.notes.append(f"acad.exe already running (pid {', '.join(map(str, pids))}); not starting another.")
            else:
                try:
                    self.plan = launch_plan()
                except Exception as e:
                    self.state = "failed"
                    self.notes.append(str(e))
                    return self.report()
            self._thread = threading.Thread(target=self._watch, args=(not pids,), daemon=True,
                                            name="civil3d-launch")
            self._thread.start()
            return self.report()

    def wait(self, seconds: float) -> dict:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.state in ("blocked", "failed"):
                break
            if self.state != "starting" and (self.request is None or self.drawing is not None
                                             or self.state == "failed"):
                break
            time.sleep(1.0)
        return self.report()

    def report(self) -> dict:
        now = time.time()
        out: dict[str, Any] = {"launch_state": self.state}
        if self.started:
            out["elapsed_s"] = round((self.ready_at or now) - self.started)
        if self.plan:
            out["started_from"] = self.plan.get("shortcut") or self.plan.get("target")
        if self.method:
            out["method"] = self.method
        if self.notes:
            out["notes"] = self.notes[-6:]
        if self.request:
            out["new_drawing_requested"] = f"{self.request['folder']}\\{self.request['name']}.dwg"
        if self.drawing is not None:
            out["new_drawing"] = self.drawing
        if self.state == "starting":
            pids = acad_pids()
            if pids and self.started and now - self.started > 60:
                w = acad_windows(pids)
                if w:
                    out["civil3d_windows"] = w
                    out["hint"] = ("Still starting. If a window above is a licence, sign-in or first-run dialog, "
                                   "it needs answering before Civil 3D can finish loading.")
            out.setdefault("next", "Civil 3D is still loading. Call c3d_launch again (it waits up to 40 s) "
                                   "or check c3d_status.")
        elif self.state == "blocked":
            out["hidden_pids"] = self.hidden_pids
            out["next"] = ("acad.exe is running with no window and does not answer: a leftover from an earlier Civil 3D "
                           "session, which also blocks a new one. Ask the person, then call c3d_launch(folder, name, "
                           "end_hidden=True) to end it and start Civil 3D. Any unsaved work in it is lost.")
        elif self.state == "ready":
            if self.request and self.drawing is None:
                out["next"] = "Civil 3D is ready; the fresh drawing is being made now. Call c3d_launch again shortly."
            elif self.request is None:
                out["next"] = "Civil 3D is ready. Start the working drawing with c3d_new_drawing(folder, name)."
        return out

    # ---- background
    def _note(self, text: str) -> None:
        self.notes.append(f"{time.strftime('%H:%M:%S')} {text}")

    def _launch(self) -> None:
        plan = self.plan or {}
        lnk = plan.get("shortcut")
        if lnk:
            # explorer.exe hands the shortcut to the desktop shell, which starts Civil 3D outside this server.
            subprocess.Popen(["explorer.exe", lnk], creationflags=0x08000000)
            self.method = "opened the Civil 3D shortcut through Windows Explorer"
            self._note(f"opened {Path(lnk).name}")
            end = time.time() + EXPLORER_GRACE_S
            while time.time() < end:
                if acad_pids():
                    return
                time.sleep(1.0)
            self._note("acad.exe did not appear after opening the shortcut; starting it directly instead.")
        cmd = f'"{plan["target"]}" {plan.get("args", "")}'.strip()
        cwd = plan.get("workdir") or str(Path(plan["target"]).parent)
        base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(cmd, cwd=cwd, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
        except OSError:  # the job does not allow breakaway
            subprocess.Popen(cmd, cwd=cwd, creationflags=base, close_fds=True)
            self._note("started as a child of this server: restarting the server may close Civil 3D.")
        self.method = "ran acad.exe directly with the Civil 3D Metric profile"
        self._note(f"started: {cmd}")

    def _watch(self, do_launch: bool) -> None:
        if HAVE_COM:
            pythoncom.CoInitialize()
        try:
            if do_launch:
                try:
                    self._launch()
                except Exception as e:
                    self.state = "failed"
                    self._note(f"could not start Civil 3D: {type(e).__name__}: {e}")
                    return
            seen_process = False
            idle_since: float | None = None
            while time.time() - (self.started or time.time()) < START_TIMEOUT_S:
                pids = acad_pids()
                if pids:
                    seen_process = True
                elif seen_process:
                    self.state = "failed"
                    self._note("acad.exe started and then closed. Check the screen or the Autodesk licence.")
                    return
                for t in close_notices():
                    self._note(f"closed the '{t}' notice (backups stay in the Drawing Recovery Manager).")
                app = get_running_app() if pids else None
                if (not do_launch and app is None and pids and time.time() - (self.started or 0) > HIDDEN_S
                        and not acad_windows(pids)):
                    self.state, self.hidden_pids = "blocked", pids
                    self._note(f"acad.exe (pid {', '.join(map(str, pids))}) has no window and has not answered for "
                               f"{HIDDEN_S:.0f} s.")
                    return
                if app is not None and is_idle(app):
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since >= SETTLE_S:
                        app = None
                        break
                else:
                    idle_since = None
                app = None
                time.sleep(2.0)
            else:
                self.state = "failed"
                self._note(f"Civil 3D did not become ready within {START_TIMEOUT_S / 60:.0f} minutes.")
                return
            self.state, self.ready_at = "ready", time.time()
            self._note("Civil 3D is answering and idle.")
        finally:
            if HAVE_COM:
                pythoncom.CoUninitialize()
        # Civil 3D reports idle before its startup notices appear (Drawing Recovery came ~2 min later on 26 Oct).
        # Keep sweeping for a while so the drawing step is not caught by a modal notice.
        end = time.time() + NOTICE_SWEEP_S
        while time.time() < end:
            for t in close_notices():
                self._note(f"closed the '{t}' notice (backups stay in the Drawing Recovery Manager).")
            time.sleep(1.0)
        if self.request:
            r = self.request
            last = None
            for attempt in (1, 2):
                for t in close_notices():
                    self._note(f"closed the '{t}' notice (backups stay in the Drawing Recovery Manager).")
                try:
                    self.drawing = self.c3d.call(self.c3d.new_drawing, r["folder"], r["name"], r["template"])
                    self._note(f"fresh drawing saved: {self.drawing.get('drawing')}")
                    return
                except Exception as e:
                    last = f"{type(e).__name__}: {e}"
                    self._note(f"making the drawing failed (attempt {attempt}): {last}")
                    time.sleep(8.0)
            self.drawing = {"error": last}
            self._note("Civil 3D started, but the fresh drawing could not be made; use c3d_new_drawing.")
