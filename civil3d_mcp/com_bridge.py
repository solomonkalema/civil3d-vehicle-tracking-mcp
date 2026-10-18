"""COM bridge to a running Civil 3D (AutoCAD) instance.

Design points
-------------
* Every COM call runs on ONE dedicated thread that has called CoInitialize.
  The MCP server is async; COM objects must not cross threads.
* AutoCAD rejects COM calls while it is busy (a command or dialog is active).
  Calls are retried on RPC_E_CALL_REJECTED / RPC_E_SERVERCALL_RETRYLATER.
* SendCommand does NOT return while a modal dialog is open (seen live with
  Vehicle Tracking, Oct 2026). v0.2 therefore sends every command through the
  Runner (runner.py): its own COM thread, plus a dialog watcher that reads and
  answers pop-ups through UI Automation while COM is blocked.
* An empty command string is refused: Enter on an empty prompt repeats the
  last command, which can re-run an erase.
* Originals are protected: drawing-changing calls refuse to run on a drawing
  that is not a working copy (see make_working_copy), unless overridden.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:  # Windows only
    import pythoncom
    import pywintypes
    import win32com.client

    HAVE_COM = True
except ImportError:  # lets the geometry tools and tests run elsewhere
    HAVE_COM = False

RPC_RETRY = {-2147418111, -2147417846}  # CALL_REJECTED, SERVERCALL_RETRYLATER
SERVER_VERSION = "0.3.0"
WORK_DIR_NAME = "Claude outputs"
WORK_TAG = "_claude"

CMDACTIVE_BITS = {
    1: "command",
    2: "transparent command",
    4: "script",
    8: "dialog box",
    16: "DDE",
    32: "AutoLISP",
    64: "ObjectARX command",
}


class Civil3DError(RuntimeError):
    pass


def attach_app():
    """Attach to the running Civil 3D on the calling thread. Never starts a second instance."""
    if not HAVE_COM:
        raise Civil3DError("pywin32 is not available. This tool only works on the Windows machine running Civil 3D.")
    app = None
    # Versioned ProgIDs first (R26 = 2027), so another installed AutoCAD registered
    # as CurVer cannot redirect us.
    for progid in ("AutoCAD.Application.26", "AutoCAD.Application.25", "AutoCAD.Application.24", "AutoCAD.Application"):
        try:
            app = win32com.client.GetActiveObject(progid)
            break
        except pywintypes.com_error:
            continue
    if app is None:
        raise Civil3DError("No running Civil 3D found. Open Civil 3D and a drawing first.")
    # Late binding so by-reference [out] VARIANTs behave the same with or without a makepy cache.
    return win32com.client.dynamic.Dispatch(app._oleobj_)


class _ComThread:
    def __init__(self) -> None:
        self._ex = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="civil3d-com", initializer=self._init)

    @staticmethod
    def _init() -> None:
        if HAVE_COM:
            pythoncom.CoInitialize()

    def run(self, fn: Callable[..., Any], *a: Any, timeout: float = 600, **kw: Any) -> Any:
        return self._ex.submit(fn, *a, **kw).result(timeout=timeout)


class Civil3D:
    """All methods are blocking and must be called through `call()` from async code."""

    def __init__(self) -> None:
        self._thread = _ComThread()
        self._app = None
        self._lock = threading.Lock()
        self.allow_original = False
        self._tmp = Path(tempfile.gettempdir()) / "civil3d_mcp"
        self._tmp.mkdir(exist_ok=True)
        from .runner import Runner

        self.runner = Runner(self)
        self.answers: dict | None = None  # answers for dialogs during the current tool call

    # ------------------------------------------------------------------ plumbing
    def call(self, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
        with self._lock:
            return self._thread.run(fn, *a, **kw)

    def _dialog_open(self) -> bool:
        r = getattr(self, "runner", None)
        return bool(r and r.active and r.active.state == "dialog")

    def _retry(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        if self._dialog_open():
            raise Civil3DError(
                f"A Civil 3D dialog is waiting for an answer (run {self.runner.active.id}). "
                "Civil 3D cannot take other calls until it is answered: use c3d_run_status / c3d_dialog_act."
            )
        end = time.time() + timeout
        while True:
            try:
                return fn()
            except pywintypes.com_error as e:  # type: ignore[name-defined]
                if e.hresult in RPC_RETRY and time.time() < end:
                    time.sleep(0.25)
                    continue
                if e.hresult in RPC_RETRY:
                    raise Civil3DError(self.busy_reason()) from e
                raise
            except AttributeError as e:
                # Late-bound COM reports a rejected call as AttributeError '<unknown>.Name' (seen live with
                # PAN active and at Vehicle Tracking prompts). Treat it as busy, not as a missing property.
                if "<unknown>" not in str(e):
                    raise
                if time.time() < end:
                    time.sleep(0.25)
                    continue
                raise Civil3DError(self.busy_reason()) from e

    # ------------------------------------------------------------------ window-side view of Civil 3D
    def main_hwnd(self) -> int:
        """Main window handle, remembered from the last time COM answered (needed when it does not)."""
        if getattr(self, "_hwnd", 0):
            return self._hwnd
        try:
            self._hwnd = int(self.app().HWND)
        except Exception:
            self._hwnd = 0
        return self._hwnd

    def busy_reason(self) -> str:
        """Explain why Civil 3D is refusing COM, read through the window (works while COM is blocked)."""
        from . import dialogs as dlg
        from . import winput

        h = getattr(self, "_hwnd", 0)
        bits = ["Civil 3D is busy and refusing COM calls."]
        if h and dlg.HAVE_WIN32:
            try:
                if not dlg.win32gui.IsWindowEnabled(h):
                    bits.append("A modal dialog is open in Civil 3D.")
                lines = winput.command_line_text(h, dlg._pid_of(h))
                if lines:
                    bits.append(f"Command line: '{lines[-1]}'.")
            except Exception:
                pass
        bits.append("If a command such as PAN or a prompt is active, c3d_clear_command cancels it from here.")
        return " ".join(bits)

    def clear_command(self, wait_s: float = 6.0) -> dict:
        """Cancel any command left running (PAN, a prompt, a half-finished command) by Esc through the window."""
        from . import winput

        h = self.main_hwnd()
        before = None
        try:
            before = self._retry(lambda: self.doc().GetVariable("CMDNAMES"), timeout=2)
            if not before:
                return {"cleared": False, "note": "no command was active"}
        except Civil3DError:
            before = "(busy: refusing COM)"
        if not h:
            raise Civil3DError("Cannot find the Civil 3D window to send Esc to.")
        end = time.time() + wait_s
        while time.time() < end:
            winput.escape(h)
            time.sleep(0.6)
            try:
                if not self._retry(lambda: self.doc().GetVariable("CMDNAMES"), timeout=1):
                    return {"cleared": True, "was": before}
            except Civil3DError:
                continue
        raise Civil3DError(f"Esc did not clear the active command ({before}). {self.busy_reason()}")

    def app(self):
        if self._app is not None:
            try:
                self._retry(lambda: self._app.Name, timeout=5)
                return self._app
            except Civil3DError:
                raise
            except Exception:
                self._app = None
        self._app = attach_app()
        try:
            self._hwnd = int(self._app.HWND)
        except Exception:
            pass
        return self._app

    def doc(self):
        app = self.app()
        try:
            d = self._retry(lambda: app.ActiveDocument)
        except pywintypes.com_error:
            d = None
        if d is None:
            raise Civil3DError("Civil 3D has no drawing open.")
        return d

    def ms(self):
        return self._retry(lambda: self.doc().ModelSpace)

    # ------------------------------------------------------------------ variants
    @staticmethod
    def vpt(x: float, y: float, z: float = 0.0):
        return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z)))

    @staticmethod
    def out_var(vt_type=None):
        """By-reference VARIANT for COM [out] parameters (late binding does not return them otherwise)."""
        t = pythoncom.VT_VARIANT if vt_type is None else vt_type
        return win32com.client.VARIANT(pythoncom.VT_BYREF | t, 0.0 if t == pythoncom.VT_R8 else None)

    def bbox(self, ent) -> list[float]:
        mn, mx = self.out_var(), self.out_var()
        self._retry(lambda: ent.GetBoundingBox(mn, mx))
        a, b = mn.value, mx.value
        return [round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3)]

    @staticmethod
    def vdoubles(vals):
        return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        r = self.runner.active
        run = None if r is None else {"run_id": r.id, "label": r.label, "state": r.state}
        if self._dialog_open():
            return {"run": run, "note": "A dialog is waiting; Civil 3D is not taking other calls. Use c3d_run_status."}
        try:
            app = self.app()
            d = self.doc()
        except Civil3DError as e:
            return {"server_version": SERVER_VERSION, "run": run, "busy": True, "reason": str(e)}
        try:
            full = self._retry(lambda: d.FullName) or "(unsaved)"
        except Exception:
            full = "(unsaved)"
        return {
            "server_version": SERVER_VERSION,
            "run": run,
            "application": self._retry(lambda: app.Name),
            "version": self._retry(lambda: app.Version),
            "drawing": full,
            "is_working_copy": self.is_working_copy(full),
            "insunits": self._retry(lambda: d.GetVariable("INSUNITS")),
            "cmdactive": self.cmdactive(),
            "documents_open": self._retry(lambda: app.Documents.Count),
        }

    def cmdactive(self) -> dict:
        v = int(self._retry(lambda: self.doc().GetVariable("CMDACTIVE"), timeout=5))
        return {"value": v, "active": [n for b, n in CMDACTIVE_BITS.items() if v & b]}

    def get_var(self, name: str):
        return self._retry(lambda: self.doc().GetVariable(name))

    def set_var(self, name: str, value) -> None:
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int) and -32768 <= value <= 32767:
            # Many system variables are 16-bit; AutoCAD rejects a VT_I4 for them.
            try:
                self._retry(lambda: self.doc().SetVariable(name, win32com.client.VARIANT(pythoncom.VT_I2, value)))
                return
            except pywintypes.com_error:
                pass
        self._retry(lambda: self.doc().SetVariable(name, value))

    # ------------------------------------------------------------------ originals
    @staticmethod
    def is_working_copy(path: str) -> bool:
        if not path:
            return False
        p = path.replace("\\", "/")
        if f"/{WORK_DIR_NAME}/" in p or Path(p).stem.endswith(WORK_TAG):
            return True
        # 'x_claude.dxf.dwg' (a DXF copied by v0.2) and drawings made by c3d_new_drawing are registered
        stem = Path(p).name.split(".")[0]
        return stem.endswith(WORK_TAG) or os.path.normcase(os.path.abspath(path)) in _registry()

    def guard_write(self) -> None:
        if self.allow_original:
            return
        full = self._retry(lambda: self.doc().FullName) or ""
        if not full:
            return  # unsaved new drawing, nothing to protect
        if not self.is_working_copy(full):
            raise Civil3DError(
                f"Refusing to change '{full}' because it is not a working copy. Call make_working_copy first "
                f"(it saves a copy into a '{WORK_DIR_NAME}' folder and switches to it), or call "
                "set_original_protection(false) if you really mean to edit the original."
            )

    def _save_as_dwg(self, d, dst: Path) -> None:
        """SaveAs as a DWG whatever the source format (a DXF source otherwise came out as 'x.dxf.dwg')."""
        try:
            self._retry(lambda: d.SaveAs(str(dst), 64))  # 64 = ac2018_dwg (current native format)
        except Exception:
            self._retry(lambda: d.SaveAs(str(dst)))
        _register(str(dst))

    def make_working_copy(self, folder: str | None = None, suffix: str = WORK_TAG, name: str | None = None) -> dict:
        """Save the active drawing as a working copy and switch to it. Always a .dwg.

        A never-saved drawing has no original to protect: it is saved straight into `folder` as `name`."""
        d = self.doc()
        src = self._retry(lambda: d.FullName)
        if not src:
            if not folder:
                raise Civil3DError("This drawing has never been saved. Give `folder` (the job folder) and optionally `name`.")
            out_dir = Path(folder)
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = _unique(out_dir / f"{Path(name or 'Drawing').stem}{suffix}.dwg")
            self._save_as_dwg(d, dst)
            self.activate()
            return {"original": None, "working_copy": str(dst), "note": "unsaved drawing saved straight into the folder"}
        if self.is_working_copy(src):
            return {"working_copy": src, "note": "already a working copy"}
        srcp = Path(src)
        out_dir = Path(folder) if folder else srcp.parent / WORK_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = srcp.name.split(".")[0] if srcp.suffix.lower() != ".dwg" else srcp.stem
        dst = _unique(out_dir / f"{name and Path(name).stem or stem}{suffix}.dwg")
        # SaveAs writes the in-memory drawing to the new path and makes it the active file.
        # The original on disk is not written.
        self._save_as_dwg(d, dst)
        self.activate()
        return {"original": src, "working_copy": str(dst)}

    def new_drawing(self, folder: str, name: str, template: str | None = None) -> dict:
        """Start a new metric drawing, save it into `folder` as `name`.dwg and make it the active working copy."""
        app = self.app()
        docs = self._retry(lambda: app.Documents)
        d = self._retry(lambda: docs.Add(template) if template else docs.Add(), timeout=60)
        self._retry(lambda: d.Activate())
        out_dir = Path(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = _unique(out_dir / f"{Path(name).stem}.dwg")
        self._save_as_dwg(d, dst)
        for var, val in (("INSUNITS", 6), ("DYNMODE", 0)):
            try:
                self.set_var(var, val)
            except Exception:
                pass
        self.lisp('(progn (setq mcp:s (entget (tblobjname "STYLE" "Standard"))) (entmod (subst (cons 3 "arial.ttf") (assoc 3 mcp:s) mcp:s)) "ok")')
        self.qsave()
        return {"drawing": str(dst), "working_copy": True, "insunits": 6, "standard_font": "arial.ttf", "template": template or "(Civil 3D default)"}

    def activate(self) -> None:
        """Bring the active document's tab to the front so the person sees the work."""
        try:
            d = self.doc()
            self._retry(lambda: d.Activate(), timeout=5)
        except Exception:
            pass

    def zoom_window(self, x1: float, y1: float, x2: float, y2: float) -> None:
        app = self.app()
        self._retry(lambda: app.ZoomWindow(self.vpt(x1, y1), self.vpt(x2, y2)))

    def open_drawing(self, path: str, read_only: bool = False) -> dict:
        app = self.app()
        docs = self._retry(lambda: app.Documents)
        for i in range(self._retry(lambda: docs.Count)):
            d = docs.Item(i)
            if os.path.normcase(d.FullName) == os.path.normcase(path):
                self._retry(lambda: d.Activate())
                return {"drawing": path, "note": "already open, activated"}
        self._retry(lambda: docs.Open(path, read_only), timeout=120)
        return {"drawing": path, "read_only": read_only}

    def qsave(self) -> str:
        self.guard_write()
        self._retry(lambda: self.doc().Save(), timeout=120)
        return self._retry(lambda: self.doc().FullName)

    # ------------------------------------------------------------------ commands
    def send(self, command: str, wait: bool = True, timeout: float = 120.0, *, label: str = "", on_finish=None) -> dict:
        """Send a command through the dialog-aware runner.

        Returns when the command ends. If it stops at a dialog the server may not
        answer by itself, raises DialogPending (the tool returns the dialog to
        Claude). With wait=False it watches for 1.5 s to catch an immediate dialog.
        """
        from .runner import DialogPending

        cmd = command.rstrip("\r\n")
        if not cmd.strip():
            raise Civil3DError("Empty command refused: Enter on an empty prompt repeats the last command.")
        if not cmd.endswith(" "):
            cmd += "\n"
        run = self.runner.start(cmd, label=label, answers=self.answers, on_finish=on_finish)
        res = self.runner.wait(run, 1.5 if not wait else min(timeout, 40.0))
        if res["status"] == "dialog_pending":
            raise DialogPending(res)
        if res["status"] == "finished":
            out = {"sent": command, "idle": True}
            if res.get("dialogs_answered"):
                out["dialogs_answered"] = res["dialogs_answered"]
            if res.get("findings"):
                out["findings"] = res["findings"]
            if "result" in res:
                out["result"] = res["result"]
            return out
        return {"sent": command, "idle": False, **res}

    def wait_idle(self, timeout: float = 120.0) -> dict:
        """Wait for the current run (or any command) to end. Never blocks more than ~40 s per call."""
        from .runner import DialogPending

        if self.runner.active is not None and self.runner.active.open:
            res = self.runner.status(min(timeout, 40.0))
            if res["status"] == "dialog_pending":
                raise DialogPending(res)
            return {"idle": res["status"] in ("finished", "cancelled", "failed"), **res}
        return self._poll_idle(min(timeout, 40.0))

    def _poll_idle(self, timeout: float = 40.0) -> dict:
        """Wait until no command is running. Reports a stuck dialog rather than hanging."""
        end = time.time() + timeout
        last = None
        # SendCommand is asynchronous: give the command up to 1 s to start before
        # trusting CMDACTIVE = 0.
        start_by = time.time() + 1.0
        while time.time() < start_by:
            try:
                if self.cmdactive()["value"]:
                    break
            except Civil3DError:
                break
            time.sleep(0.1)
        while time.time() < end:
            try:
                st = self.cmdactive()
            except Civil3DError:
                time.sleep(0.5)  # busy: modal dialog or long computation
                last = {"value": None, "active": ["busy (rejecting calls)"]}
                continue
            if st["value"] == 0:
                return {"idle": True}
            last = st
            time.sleep(0.3)
        return {
            "idle": False,
            "state": last,
            "hint": "Still running. Call c3d_wait_idle again; dialogs are reported as they appear.",
        }

    def lisp(self, expr: str, timeout: float = 60.0) -> dict:
        """Evaluate one AutoLISP expression and return its printed result.

        The result is written to a temp file by LISP, so quotes and long values
        come back intact (no command-line echo parsing).
        """
        out = self._tmp / f"lisp_{int(time.time() * 1000)}.txt"
        if out.exists():
            out.unlink()
        fp = str(out).replace("\\", "/")
        wrapped = (
            "(progn (vl-load-com)"
            f" (setq mcp:r (vl-catch-all-apply (function (lambda () {expr})) nil))"
            f' (setq mcp:f (open "{fp}" "w"))'
            ' (if (vl-catch-all-error-p mcp:r)'
            '   (write-line (strcat "LISP-ERROR: " (vl-catch-all-error-message mcp:r)) mcp:f)'
            "   (write-line (vl-prin1-to-string mcp:r) mcp:f))"
            " (close mcp:f) (princ))"
        )
        state = self.send(wrapped, wait=True, timeout=timeout)
        end = time.time() + 5
        while not out.exists() and time.time() < end:
            time.sleep(0.1)
        if not out.exists():
            return {"ok": False, "result": None, "state": state, "hint": "No result file; the expression may be waiting for input."}
        text = out.read_text(encoding="utf-8", errors="replace").strip()
        out.unlink(missing_ok=True)
        if text.startswith("LISP-ERROR:"):
            return {"ok": False, "error": text[12:].strip()}
        return {"ok": True, "result": text}

    # ------------------------------------------------------------------ layers and entities
    def ensure_layer(self, name: str, color: int | None = None):
        d = self.doc()
        layers = self._retry(lambda: d.Layers)
        try:
            lay = layers.Item(name)
        except pywintypes.com_error:
            lay = self._retry(lambda: layers.Add(name))
        if color is not None:
            self._retry(lambda: setattr(lay, "Color", int(color)))
        return lay

    def add_polyline(self, pts, layer: str, color: int | None = None, closed: bool = False, entity_color: int | None = None) -> str:
        self.guard_write()
        self.ensure_layer(layer, color)
        flat = [c for p in pts for c in (p[0], p[1])]
        pl = self._retry(lambda: self.ms().AddLightWeightPolyline(self.vdoubles(flat)))
        self._retry(lambda: setattr(pl, "Layer", layer))
        if closed:
            self._retry(lambda: setattr(pl, "Closed", True))
        if entity_color is not None:
            self._retry(lambda: setattr(pl, "Color", int(entity_color)))
        return self._retry(lambda: pl.Handle)

    def add_text(self, text: str, x: float, y: float, height: float, layer: str) -> str:
        self.guard_write()
        self.ensure_layer(layer)
        t = self._retry(lambda: self.ms().AddText(text, self.vpt(x, y), height))
        self._retry(lambda: setattr(t, "Layer", layer))
        return self._retry(lambda: t.Handle)

    def add_hatch(self, outer_handle: str, layer: str, color: int = 1, transparency: int = 60) -> str:
        """Solid hatch inside a closed polyline (used to shade swept envelopes)."""
        self.guard_write()
        self.ensure_layer(layer)
        h = self._retry(lambda: self.ms().AddHatch(1, "SOLID", True))  # 1 = acHatchPatternTypePreDefined
        boundary = self.entity(outer_handle)
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [boundary])
        self._retry(lambda: h.AppendOuterLoop(arr))
        self._retry(lambda: h.Evaluate())
        self._retry(lambda: setattr(h, "Layer", layer))
        self._retry(lambda: setattr(h, "Color", int(color)))
        try:
            self._retry(lambda: setattr(h, "EntityTransparency", str(transparency)))
        except Exception:
            pass
        return self._retry(lambda: h.Handle)

    def entity(self, handle: str):
        return self._retry(lambda: self.doc().HandleToObject(handle))

    def delete(self, handle: str) -> None:
        self.guard_write()
        self._retry(lambda: self.entity(handle).Delete())

    def lisp_data(self, expr: str, timeout: float = 120.0):
        """Evaluate LISP and parse the printed result into Python lists/strings/numbers."""
        from .lispdata import parse

        res = self.lisp(expr, timeout)
        if not res.get("ok"):
            raise Civil3DError(f"LISP read failed: {res.get('error') or res.get('hint') or res}")
        return parse(res["result"])

    def layers(self) -> list[dict]:
        """Layer table read inside Civil 3D in one go (code 62 < 0 = off, 70 bit 1 = frozen, bit 4 = locked)."""
        rows = self.lisp_data(
            '(progn (setq mcp:L nil mcp:r (tblnext "LAYER" T))'
            " (while mcp:r (setq mcp:L (cons (list (cdr (assoc 2 mcp:r)) (cdr (assoc 62 mcp:r)) (cdr (assoc 70 mcp:r))) mcp:L)"
            ' mcp:r (tblnext "LAYER"))) (reverse mcp:L))'
        ) or []
        return [
            {"name": n, "on": c > 0, "frozen": bool(f & 1), "locked": bool(f & 4), "color": abs(c)}
            for n, c, f in rows
        ]

    def select_on_layers(self, layer_pattern: str, types: str | None = None) -> list:
        """All model-space entities on layers matching a wildcard pattern (e.g. 'A-WALL*,KERB')."""
        d = self.doc()
        ssets = self._retry(lambda: d.SelectionSets)
        name = "MCP_SEL"
        try:
            ssets.Item(name).Delete()
        except pywintypes.com_error:
            pass
        ss = self._retry(lambda: ssets.Add(name))
        codes, values = [8, 410], [layer_pattern, "Model"]
        if types:
            codes.append(0)
            values.append(types)
        ft = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, codes)
        fd = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, values)
        p = self.vpt(0, 0)
        self._retry(lambda: ss.Select(5, p, p, ft, fd), timeout=120)  # 5 = acSelectionSetAll
        n = self._retry(lambda: ss.Count)
        return [self._retry(lambda i=i: ss.Item(i)) for i in range(n)]

    def png_out(self, path: str, window: tuple[float, float, float, float] | None = None) -> dict:
        """Export the current view (or a zoom window) to PNG without dialogs."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fp = path.replace("\\", "/")
        old = self.get_var("FILEDIA")
        ctr, size = self.get_var("VIEWCTR"), self.get_var("VIEWSIZE")  # the person's view, restored after
        used = None
        try:
            self.set_var("FILEDIA", 0)
            self.lisp("(sssetfirst nil nil)")  # a pickfirst selection made one export ignore its window
            if window:
                x1, y1, x2, y2 = window
                try:
                    self.zoom_window(x1, y1, x2, y2)
                except Exception:
                    self.send(f'(command "_.ZOOM" "_W" "{x1},{y1}" "{x2},{y2}")')
                c, h = self.get_var("VIEWCTR"), float(self.get_var("VIEWSIZE"))
                used = {"center": [round(c[0], 3), round(c[1], 3)], "view_height_m": round(h, 3)}
            if Path(path).exists():
                Path(path).unlink()
            self.send(f'(command "_.PNGOUT" "{fp}" "")')
        finally:
            self.set_var("FILEDIA", old)
            try:
                self.send(f'(command "_.ZOOM" "_C" "{ctr[0]},{ctr[1]}" {float(size)})')
            except Exception:
                pass
        out = {"png": path, "exists": Path(path).exists(), "view_restored": True}
        if used:
            out["window_used"] = used
            try:
                from PIL import Image

                with Image.open(path) as im:
                    out["pixels"] = list(im.size)
                    out["metres_per_pixel"] = round(used["view_height_m"] / im.size[1], 6)
            except Exception:
                pass
        return out


_REG_FILE = None


def _reg_path() -> Path:
    from .catalogue import HOME

    return HOME / "working_copies.json"


def _registry() -> set[str]:
    import json

    f = _reg_path()
    try:
        return set(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _register(path: str) -> None:
    import json

    reg = _registry()
    reg.add(os.path.normcase(os.path.abspath(path)))
    f = _reg_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(reg), indent=1), encoding="utf-8")


def _unique(dst: Path) -> Path:
    n = 2
    base = dst
    while dst.exists():
        dst = base.with_name(f"{base.stem}_{n}{base.suffix}")
        n += 1
    return dst


def copy_file_to_working(src: str) -> str:
    """Copy a drawing on disk into its Claude outputs folder without opening it."""
    s = Path(src)
    out = s.parent / WORK_DIR_NAME
    out.mkdir(exist_ok=True)
    dst = out / f"{s.stem}{WORK_TAG}{s.suffix}"
    n = 2
    while dst.exists():
        dst = out / f"{s.stem}{WORK_TAG}_{n}{s.suffix}"
        n += 1
    shutil.copy2(s, dst)
    return str(dst)
