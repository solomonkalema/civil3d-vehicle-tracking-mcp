"""Dialog-aware command runs.

Why this exists: COM SendCommand does not return while Civil 3D shows a modal
dialog, so a single COM thread (v0.1) froze the whole server behind any pop-up.
Here every command is sent from its own COM thread, and while it runs the
server watches Civil 3D's windows through Windows UI Automation, which keeps
working while COM is blocked.

When a dialog appears:
  1. it is read in full (dialogs.snapshot) and matched against the catalogue;
  2. if the catalogue says it is a confirmed, unchanged dialog AND the task
     supplied the answer, the server answers it and logs it;
  3. otherwise the run pauses and the tool returns the dialog's contents.
     Claude decides from the task (c3d_dialog_act) or asks the person.

Tool calls never block for more than ~40 s (the desktop bridge times out at
60 s). A run still going after that returns status "running"; continue with
c3d_run_status.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as _dt
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import dialogs as dlg
from . import winput
from .catalogue import HOME, Catalogue, compact, resolve_answer

try:
    import pythoncom
    import pywintypes

    HAVE_COM = True
except ImportError:
    HAVE_COM = False

LOG_DIR = HOME / "run_logs"
MAX_BLOCK_S = 40.0  # never hold a tool call longer than this
RPC_RETRY = {-2147418111, -2147417846}


def _wait_closed(hwnd: int, timeout: float = 3.0) -> bool:
    """True once the answered window has closed, hidden, or been covered by another modal."""
    if not dlg.HAVE_WIN32:
        time.sleep(0.6)
        return True
    end = time.time() + timeout
    while time.time() < end:
        w = dlg.win32gui
        if not w.IsWindow(hwnd) or not w.IsWindowVisible(hwnd) or not w.IsWindowEnabled(hwnd):
            time.sleep(0.3)
            return True
        time.sleep(0.15)
    return False


class DialogPending(Exception):
    """Raised inside a tool when a command it sent stopped at a dialog."""

    def __init__(self, payload: dict) -> None:
        super().__init__(payload.get("summary", "dialog pending"))
        self.payload = payload


class Run:
    _n = 0

    def __init__(self, command: str, label: str, answers: dict | None, on_finish: Callable[[], Any] | None) -> None:
        Run._n += 1
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.id = f"{stamp}-{Run._n}"
        self.command = command
        self.label = label or command.split("\n")[0][:40]
        self.answers = answers or {}
        self.on_finish = on_finish
        self.started = time.time()
        self.future: cf.Future | None = None
        self.pid = 0
        self.main_hwnd = 0
        self.baseline: set[int] = set()
        self.state = "running"  # running | dialog | prompt | finished | failed | cancelled
        self.pending: dict | None = None  # {"id", "snapshot", "match"}
        self.dialog_seq = 0
        self.findings: list[str] = []
        self.answered: list[dict] = []
        self.result: Any = None
        self.error: str | None = None
        self.future_done_at: float | None = None
        self.inputs: list[str] = []  # answers for command-line prompts, sent in order
        self.last_event = time.time()  # last dialog answered / input sent: prompts are judged from here
        self.prompt_patterns: list[str] | None = None
        self.prompt_text: str | None = None
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = LOG_DIR / f"{self.id}.jsonl"

    def log(self, event: str, **data: Any) -> None:
        row = {"t": _dt.datetime.now().isoformat(timespec="seconds"), "event": event, **data}
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    @property
    def open(self) -> bool:
        return self.state in ("running", "dialog", "prompt")


class Runner:
    def __init__(self, c3d) -> None:
        self.c3d = c3d
        self._ex = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="civil3d-send", initializer=self._init)
        self._app = None
        self.active: Run | None = None
        self.catalogue = Catalogue()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ send thread
    @staticmethod
    def _init() -> None:
        if HAVE_COM:
            pythoncom.CoInitialize()

    def _send(self, cmd: str) -> None:
        """Runs on the send thread. Blocks while a modal dialog is open; that is expected."""
        if self._app is None:
            from .com_bridge import attach_app

            self._app = attach_app()
        end = time.time() + 30
        while True:
            try:
                self._app.ActiveDocument.SendCommand(cmd)
                return
            except pywintypes.com_error as e:  # type: ignore[name-defined]
                if e.hresult in RPC_RETRY and time.time() < end:
                    time.sleep(0.25)
                    continue
                if e.hresult not in RPC_RETRY:
                    self._app = None  # reattach next time
                raise

    # ------------------------------------------------------------ lifecycle
    def busy(self) -> bool:
        return self.active is not None and self.active.open

    def start(self, command: str, *, label: str = "", answers: dict | None = None, on_finish: Callable[[], Any] | None = None,
              inputs: list[str] | None = None, prompt_patterns: list[str] | None = None) -> Run:
        """Called on the main COM thread."""
        if self.busy() and self.active.state == "running":
            self.wait(self.active, 1.0)  # a command sent without waiting may simply have ended
        if self.busy():
            a = self.active
            raise dlg.DialogError(
                f"Run {a.id} ('{a.label}') is still {a.state}. Finish it first: c3d_run_status, "
                "c3d_dialog_act for a waiting dialog, or c3d_run_cancel."
            )
        run = Run(command, label, answers, on_finish)
        run.inputs = list(inputs or [])
        run.prompt_patterns = prompt_patterns
        app = self.c3d.app()
        run.main_hwnd = int(self.c3d._retry(lambda: app.HWND))
        if dlg.HAVE_WIN32:
            run.pid = dlg._pid_of(run.main_hwnd)
            run.baseline = set(dlg.top_windows(run.pid))
        run.log("start", command=command, label=run.label, answers=answers)
        run.future = self._ex.submit(self._send, command)
        self.active = run
        return run

    def wait(self, run: Run, budget_s: float = 25.0) -> dict:
        """Watch the run until it finishes, stops at a dialog or prompt, or the budget runs out.
        Called on the main COM thread (it reads CMDACTIVE once SendCommand has returned)."""
        budget_s = max(1.0, min(budget_s, MAX_BLOCK_S))
        end = time.time() + budget_s
        while time.time() < end:
            if run.state == "dialog" and run.pending:
                return self.pending_payload(run)
            # 1. dialogs first: they are what blocks everything else
            if dlg.HAVE_WIN32 and run.pid:
                found = dlg.new_dialogs(run.pid, run.main_hwnd, run.baseline)
                if found["hwnds"]:
                    time.sleep(0.4)  # let it finish drawing
                    found = dlg.new_dialogs(run.pid, run.main_hwnd, run.baseline)
                    if found["hwnds"]:
                        out = self._handle_dialog(run, found["hwnds"])
                        if out is not None:
                            return out
                        continue
            # 2. SendCommand still blocked and no dialog: the command may be waiting at a prompt.
            #    COM is refused in that state, so read the prompt and answer it through the window.
            fut = run.future
            if fut is not None and not fut.done() and dlg.HAVE_WIN32 and run.main_hwnd:
                out = self._prompt_while_blocked(run)
                if out is not None:
                    return out
            # 3. has SendCommand come back?
            if fut is not None and fut.done():
                if fut.exception() is not None:
                    run.state, run.error = "failed", f"{type(fut.exception()).__name__}: {fut.exception()}"
                    run.log("failed", error=run.error)
                    return self._final(run)
                if run.future_done_at is None:
                    run.future_done_at = time.time()
                try:
                    st = self.c3d.cmdactive()
                except Exception:
                    time.sleep(0.3)
                    continue
                if st["value"] == 0:
                    return self._finish(run)
                if time.time() - run.future_done_at > 1.0 and not (st["value"] & 8) and run.inputs:
                    text = run.inputs.pop(0)
                    run.log("input", text=text, by="server (queued)")
                    run.future_done_at = None
                    run.future = self._ex.submit(self._send, text if text.endswith((" ", "\n")) else text + "\n")
                    time.sleep(0.3)
                    continue
                if time.time() - run.future_done_at > 2.0 and not (st["value"] & 8):
                    run.state = "prompt"
                    names = ""
                    try:
                        names = self.c3d.get_var("CMDNAMES")
                    except Exception:
                        pass
                    run.log("prompt", cmdnames=names)
                    return {
                        "status": "waiting_at_prompt",
                        "run_id": run.id,
                        "command_active": names,
                        "next": "The command is waiting for command-line input. Send it with c3d_run_input, or cancel with c3d_run_cancel.",
                    }
            time.sleep(0.3)
        return {"status": "running", "run_id": run.id, "elapsed_s": round(time.time() - run.started), "next": "Still running with no dialog. Call c3d_run_status to keep watching."}

    def _prompt_while_blocked(self, run: Run) -> dict | None:
        """Handle a command-line prompt while COM is blocked (SendCommand has not returned)."""
        try:
            if not dlg.win32gui.IsWindowEnabled(run.main_hwnd):
                return None  # a modal dialog is up; the dialog branch deals with it
        except Exception:
            return None
        quiet = time.time() - run.last_event
        if quiet < 1.0:
            return None
        lines = winput.command_line_text(run.main_hwnd, run.pid)
        prompt = winput.looks_like_prompt(lines, run.prompt_patterns)
        if run.inputs and (prompt or quiet > 3.0):
            text = run.inputs.pop(0)
            ok = winput.send_text(run.main_hwnd, text if text.endswith((" ", "\n")) else text + "\n")
            run.log("input", text=text, by="server (window input)", prompt=prompt, accepted=ok)
            run.last_event = time.time()
            time.sleep(0.4)
            return None
        if prompt and not run.inputs and quiet > 2.0:
            run.state = "prompt"
            run.prompt_text = prompt
            run.log("prompt", prompt=prompt, blocked=True)
            return {
                "status": "waiting_at_prompt",
                "run_id": run.id,
                "prompt": prompt,
                "next": "The command is waiting for command-line input. Send it with c3d_run_input (typed through the window, "
                        "so it works while Civil 3D refuses COM), or cancel with c3d_run_cancel.",
            }
        return None

    def _handle_dialog(self, run: Run, hwnds: list[int]) -> dict | None:
        snap = dlg.snapshot(hwnds)
        # The floating command line shows up as a window while a modal is open: it is not a dialog.
        cmdline = [w["hwnd"] for w in snap["windows"] if winput.is_command_line(w)]
        if cmdline:
            run.baseline.update(cmdline)
            keep = [h for h in hwnds if h not in cmdline]
            if not keep:
                return None
            snap = dlg.snapshot(keep)
            hwnds = keep
        m = self.catalogue.match(snap)
        self.catalogue.observe(snap, m)
        e = m.get("entry")
        run.dialog_seq += 1
        did = f"{run.id}-d{run.dialog_seq}"
        run.log("dialog", dialog_id=did, match=m["status"], entry=e and e["id"], fingerprint=m["fingerprint"], dialog=compact(snap))
        actions, reason = resolve_answer(m, run.answers)
        if actions is not None:
            try:
                done = dlg.perform(snap, actions)
            except Exception as ex:
                reason = f"the task's answer could not be applied ({ex})"
                actions = None
            else:
                spec = run.answers.get(e["id"]) if isinstance(run.answers.get(e["id"]), dict) else {}
                finding = spec.get("record_as_finding") if spec else None
                if e.get("kind") == "judgement" and not finding:
                    finding = f"{e.get('title')}: {e.get('description')}"
                if finding:
                    run.findings.append(finding)
                run.answered.append({"dialog_id": did, "entry": e["id"], "by": "server (task answer)", "actions": done})
                run.log("answered", dialog_id=did, by="server", entry=e["id"], actions=done, finding=finding)
                if _wait_closed(hwnds[0]):
                    run.last_event = time.time()
                    return None
                # one retry: a click is occasionally lost while the dialog is still drawing
                try:
                    dlg.perform(dlg.snapshot([h for h in hwnds if dlg.win32gui.IsWindow(h)] or hwnds), actions)
                    run.log("retried", dialog_id=did, actions=actions)
                except Exception:
                    pass
                if _wait_closed(hwnds[0]):
                    run.last_event = time.time()
                    return None
                reason = "the task's answer was applied twice but the dialog stayed open"
                snap = dlg.snapshot([h for h in hwnds if dlg.win32gui.IsWindow(h)] or hwnds)
                run.log("still_open", dialog_id=did)
        run.state = "dialog"
        run.pending = {"id": did, "snapshot": snap, "match": m, "why_not_auto": reason}
        return self.pending_payload(run)

    def pending_payload(self, run: Run) -> dict:
        p = run.pending or {}
        m = p.get("match", {})
        e = m.get("entry") or {}
        cat = {"match": m.get("status"), "why_not_answered_automatically": p.get("why_not_auto")}
        if e:
            cat.update({k: e.get(k) for k in ("id", "description", "kind", "question", "answer_format", "notes") if e.get(k)})
        snap = p.get("snapshot", {})
        unreadable = [u for w in snap.get("windows", []) for u in w.get("unreadable", [])]
        return {
            "status": "dialog_pending",
            "run_id": run.id,
            "dialog_id": p.get("id"),
            "dialog": compact(snap),
            "catalogue": cat,
            "unreadable_parts": unreadable or None,
            "next": (
                "Decide from the task and answer with c3d_dialog_act(dialog_id, actions, reason). "
                "If the task does not settle it, ask the person who started the task; the run waits. "
                "Use c3d_dialog_image if a part of the dialog has no readable text. "
                "Pass learn={...} on the first confirmed sighting so the catalogue records it."
            ),
        }

    def act(self, dialog_id: str, actions: list[dict], reason: str, learn: dict | None = None, finding: str | None = None, budget_s: float = 25.0) -> dict:
        run = self.active
        if run is None or run.state != "dialog" or not run.pending or run.pending["id"] != dialog_id:
            raise dlg.DialogError(f"Dialog {dialog_id} is not the one waiting. Call c3d_run_status for the current state.")
        old = run.pending["snapshot"]
        hwnds = [w["hwnd"] for w in old["windows"] if dlg.win32gui.IsWindow(w["hwnd"])] if dlg.HAVE_WIN32 else []
        if not hwnds:
            raise dlg.DialogError("The dialog has closed on its own. Call c3d_run_status.")
        snap = dlg.snapshot(hwnds)  # fresh read: values may have changed since it was reported
        if learn:
            learn = dict(learn)
            entry_id = learn.pop("id", None) or (run.pending["match"].get("entry") or {}).get("id")
            if not entry_id or entry_id.startswith("unknown."):
                raise dlg.DialogError("learn needs an 'id' for this dialog, e.g. {'id': 'acad.proxy_notice', ...}.")
            allowed = {"title", "text_contains", "description", "kind", "question", "workflows", "fixed_actions", "notes", "confirmed"}
            bad = set(learn) - allowed
            if bad:
                raise dlg.DialogError(f"learn does not take {sorted(bad)}; use {sorted(allowed | {'id'})}.")
            self.catalogue.learn(snap, entry_id, **learn)
            run.log("learned", dialog_id=dialog_id, entry=entry_id, details=learn)
        done = dlg.perform(snap, actions)
        if finding:
            run.findings.append(finding)
        run.answered.append({"dialog_id": dialog_id, "entry": (run.pending["match"].get("entry") or {}).get("id"), "by": "Claude", "reason": reason, "actions": done})
        run.log("answered", dialog_id=dialog_id, by="Claude", reason=reason, actions=done, finding=finding)
        run.pending = None
        run.state = "running"
        if not _wait_closed(hwnds[0]):
            try:
                dlg.perform(dlg.snapshot([h for h in hwnds if dlg.win32gui.IsWindow(h)] or hwnds), actions)
                run.log("retried", dialog_id=dialog_id, actions=actions)
            except Exception:
                pass
            if not _wait_closed(hwnds[0]):
                run.log("still_open", dialog_id=dialog_id)
        run.last_event = time.time()
        return self.wait(run, budget_s)

    def send_input(self, text: str, budget_s: float = 25.0) -> dict:
        run = self.active
        if run is None or run.state not in ("prompt", "running"):
            raise dlg.DialogError("No run is waiting at a command-line prompt.")
        if not text.strip():
            raise dlg.DialogError("Empty input refused: Enter on an empty prompt repeats the last command.")
        payload = text if text.endswith((" ", "\n")) else text + "\n"
        run.state = "running"
        run.last_event = time.time()
        sent = False
        if dlg.HAVE_WIN32 and run.main_hwnd:
            try:
                sent = winput.send_text(run.main_hwnd, payload)
            except Exception as ex:
                run.log("input_failed", text=text, route="window", error=str(ex))
        if sent:
            run.log("input", text=text, route="window")
        else:  # fall back to COM (works only once SendCommand has returned)
            run.log("input", text=text, route="com")
            run.future_done_at = None
            run.future = self._ex.submit(self._send, payload)
        return self.wait(run, budget_s)

    def cancel(self) -> dict:
        run = self.active
        if run is None or not run.open:
            return {"status": "nothing to cancel"}
        if run.state == "dialog":
            raise dlg.DialogError("A dialog is open. Close it with c3d_dialog_act (e.g. click Cancel) so its answer is recorded.")
        if dlg.HAVE_WIN32 and run.main_hwnd:
            winput.escape(run.main_hwnd)  # works while COM is blocked (prompts, PAN)
        else:
            run.future = self._ex.submit(self._send, "\x03\x03")
        time.sleep(1.0)
        run.state = "cancelled"
        run.log("cancelled")
        return self._final(run)

    def _finish(self, run: Run) -> dict:
        run.state = "finished"
        if run.on_finish is not None:
            try:
                run.result = run.on_finish()
            except Exception as ex:
                run.result = {"error": f"post-run step failed: {type(ex).__name__}: {ex}"}
            self.active = run  # post-run steps may have sent their own small commands
        run.log("finished", result=run.result, findings=run.findings)
        return self._final(run)

    def _final(self, run: Run) -> dict:
        out = {
            "status": run.state,
            "run_id": run.id,
            "elapsed_s": round(time.time() - run.started, 1),
            "dialogs_answered": run.answered,
            "findings": run.findings or None,
            "log": str(run.log_file),
        }
        if run.error:
            out["error"] = run.error
        if run.result is not None:
            out["result"] = run.result
        return out

    def status(self, budget_s: float = 25.0) -> dict:
        run = self.active
        if run is None:
            return {"status": "idle", "note": "No run has been started since the server started."}
        if not run.open:
            return self._final(run)
        if run.state == "prompt":
            run.state = "running"
            run.future_done_at = None
        return self.wait(run, budget_s)

    def dialog_image(self, dialog_id: str) -> list[Path]:
        run = self.active
        if run is None or not run.pending or run.pending["id"] != dialog_id:
            raise dlg.DialogError(f"Dialog {dialog_id} is not the one waiting.")
        out = []
        for i, w in enumerate(run.pending["snapshot"]["windows"]):
            out.append(dlg.capture(w["hwnd"], LOG_DIR / f"{dialog_id}-w{i + 1}.png"))
        return out
