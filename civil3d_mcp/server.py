"""Civil 3D MCP server.

Core tools give general access to a running Civil 3D session over COM.
Domain modules (swept paths, Vehicle Tracking, junctions, roads) build on them.

Run:  python -m civil3d_mcp.server
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from pathlib import Path
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image as _Image
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image as _Image

from shapely.geometry import MultiPolygon, Point, Polygon

from . import circulation as circ
from . import civil as civ
from . import dwg
from . import geometry as geo
from . import production as prod
from . import roads
from . import section as sec
from . import vt
from .catalogue import HOME
from .com_bridge import Civil3D, Civil3DError
from .launcher import Launcher
from . import watchdog
from .dialogs import DialogError
from .runner import DialogPending
from .vehicles import Vehicle, all_vehicles, get_vehicle, save_user_vehicle

INSTRUCTIONS = """
Civil 3D over COM, for transport engineering work (TIAs, swept paths, junctions, road design). Server v0.9.0.

Road design production (v0.8, data-shortcut road projects):
- Read before changing anything: road_shortcut_project(folder) for the project on disk, then road_report_file(path)
  per drawing (source alignment -> corridor -> PnP -> CS). 'needs_sync' lists stale or broken data references.
- road_sheets reads each sheet's plot set-up, viewport scale (paper mm per metre) and title-block attributes;
  c3d_pdf_page shows an issued PDF sheet. Compare the drawn scale with the title block's stated scale.
- Never write into a shared project folder (listed in shared_roots.json, e.g. a project folder shared with colleagues).
  c3d_make_working_copy puts copies of drawings there at the same relative path in the private staging folder,
  which holds a copy of the project tree so relative xrefs still load. The person reviews and merges them.
  Saves, new drawings and copies into a shared folder are refused (v0.8.1).
- A working copy of a SOURCE drawing (alignment, corridor) is not what the sheets reference: geometry changes reach
  the sheets only after the edited source replaces the original and references are synchronised. Agree that
  promote step with the person before editing geometry. Section 11 of c3d_knowledge has the project map.
- Large corridor drawings take 30-60 s to open; Civil 3D refuses COM calls meanwhile ('call rejected'). Retry.

Road production steps (v0.9, from a road pilot of 27 Oct 2026). Use these tools, not hand-written LISP or
outside scripts, so the working-copy and shared-folder guards always apply:
- task_preflight('road_production') first, and ask its questions once (which road, staging copy, promote step
  agreed, title-block values by the client's initials rule, plot style, output folder).
- Order per road, each on the staging copy: corridor drawing -> road_sync_references -> road_rebuild_corridors
  -> c3d_save; then each sheet drawing (PnP, CS) -> road_sync_references -> road_set_titleblock -> c3d_save ->
  road_plot_sheets(folder) -> look at every PDF with c3d_pdf_page next to the issued sheet before calling it done.
- road_sync_references may stop at 'Object Name Change' (a source object was renamed). It is the person's call
  when the source is someone else's work: quote it, answer with c3d_blockers(click=..., reason=...).
- road_plot_sheets checks the plot style is installed, REGENALLs first (profile lines were missing without it),
  plots one -PLOT per layout with BACKGROUNDPLOT 0, never over a file a PDF viewer holds open (it picks 'v2'),
  and returns 'left' when the rest won't fit in one call. Plot Progress windows are waited out, never cancelled.
- road_quantities reads cut/fill and every material from the sample lines' material sections (average end area),
  matching the Total Volume Table within 0.3% without adding or exploding a table. road_stakeout and
  road_setout_data write the stake-out table and curve/PVI data (CSV/JSON into staging).
- c3d_quit_civil3d restarts cleanly between batches (refuses while anything is unsaved); never close data-shortcut
  drawings one by one. c3d_focus brings Civil 3D to the front for computer use: never open Civil 3D from computer
  use (open_application started a second Civil 3D on 27 Oct).
- c3d_status says 'restart_needed' when newer server code is installed than is running: tell the person, and
  don't work round the missing tools with scripts in the meantime.

Ground rules:
- Never edit the user's original drawing. Call c3d_make_working_copy first; drawing-changing tools refuse otherwise.
- Never send an empty command (Enter repeats the last command). c3d_command refuses it.
- Never start a second Civil 3D. If none is running, c3d_launch(folder, name) starts Civil 3D 2027 Metric and
  saves a fresh working drawing there. Starting takes 1-3 minutes in the background: call c3d_launch again
  until launch_state is 'ready' and new_drawing is filled. If Civil 3D is already open it just makes the drawing.
- After each successful step that changes the drawing, save, so finished work is never lost.
- Units are metres (INSUNITS 6). Traffic drives on the left in Uganda.

Pop-ups Claude cannot see (v0.7). Some Civil 3D 2027 dialogs are WebView2 windows that never appear in computer-use
screenshots. The server sees them, so never rely on a screenshot to say "no dialog":
- Every step is watched: a pop-up holding Civil 3D is read and, by one rule set, safe notices and plain OK notices
  are closed and Cancel is pressed where offered (logged with a picture in run_logs/watchdog.jsonl). Anything that
  mentions saving, overwriting, deleting, licences, sign-in etc., or doesn't fit, is never answered: the step
  returns 'blockers' with the full text and buttons.
- On such a blocker: quote it to the person. If they may be away, stop the task there and send them a push
  notification quoting the dialog. Answer only on their word, with c3d_blockers(click=..., hwnd=..., reason=...).
- A step or status that is busy for no visible reason: c3d_blockers() to read everything holding Civil 3D, and
  c3d_screen() for a picture of the Civil 3D window as it really is on the monitor.
- A sweeper closes catalogued notices (watchdog.SAFE_NOTICES, e.g. 'Drawing Recovery' after a crash) all the time.

Pop-ups (dialogs) are part of the work, not something to avoid:
- Every command runs through a dialog watcher. No screen access is needed.
- A dialog is answered by the server only if it is a confirmed, unchanged catalogue entry AND the task
  supplied the answer. Otherwise the tool returns status 'dialog_pending' with the dialog's full contents.
- On 'dialog_pending': read it against the task. If the task settles it, answer with c3d_dialog_act and give
  your reason. If it does not, or the dialog says something the task did not expect (a warning, a finding),
  stop and ask the person who started the task, quoting the dialog. Never guess.
- On the first live sighting of a dialog, pass learn={...} to c3d_dialog_act so the catalogue records it.
- Before a task, call task_preflight(workflow) and ask its questions once, together. Pass the answers to the
  run tool so known dialogs need no round trip.
- A run that is still going returns 'running': continue with c3d_run_status. Tool calls never block > 40 s,
  and c3d_status always answers within seconds, reporting the step that is busy (v0.5).
- A dialog closed by the person or by Civil 3D is forgotten automatically and the run carries on; 'Please Wait'
  splashes are waited out, not reported. If the state still looks wrong: c3d_run_cancel(force=True).
- Stuck command line (PAN, a prompt, an unfinished LISP '(_>'): c3d_clear_command, which works even while
  another step holds the server.
- c3d_knowledge has the best-practice notes (Vehicle Tracking settings, AutoCAD variables, what to ask).

Swept paths, two routes:
1. Python engine (sp_* tools): fast, no dialogs. Use sp_check_guide to test a guide line and clearances
   before any Vehicle Tracking run, and sp_draw_swept_path to draw an envelope straight from DWG linework.
   Vehicle dimensions are indicative until matched to the VT vehicle diagram (sp_define_vehicle).
2. Vehicle Tracking for report figures: vt_follow_run (dialogs handled as above), then vt_check_path
   against obstacle layers, since Follow can drop points and leave the guide line.
Report clearances against 0.3 m. Report anything that only just fits, and any speed reduction, as a finding.

Swept-path task flow (v0.3; the person should never need to touch Civil 3D):
1. c3d_new_drawing(job folder, name) or c3d_make_working_copy(folder=job folder). Outputs go in the job folder.
2. vt_prepare_drawing, then vt_attach_pdf_page (or vt_attach_image) and check one dimensioned line.
3. Obstacles as polylines on OBS-*/SP-OBS-* layers; sp_parked_vehicles for occupied bays. Many lines at once
   (walls and columns traced from the PDF): c3d_draw_polylines or c3d_import_dxf, one call. Never paste
   long LISP: c3d_lisp refuses unbalanced brackets and sends anything long through a file.
4. Design vehicles come from the TIA (its design-vehicle table), never chosen here. A single-vehicle run is not
   acceptable: run the standard DV-1 car AND every other design vehicle the TIA names. Find each one's
   library path with vt_vehicle_library.
5. For each vehicle and stage: sp_build_guide -> sp_check_guide (reference auto = front axle forwards, as Follow
   steers) -> vt_follow_run(guide_handle=..., vehicle=..., vehicle_path=...) -> vt_check_path ->
   vt_insert_profile once per vehicle (no x, y: placed beside the plan). Save after every good run.
6. Full cycle for every vehicle that parks: ingress -> park -> egress, same vehicle, one connected sequence.
   Drive in from the public road in forward gear, park in a proposed bay (the most constrained bay of each
   bay type, e.g. the deepest, narrowest or furthest), then leave in forward gear back to the road. Stages
   may be forward in, reverse into the bay (direction Reverse), forward out. Check the whole sequence with
   sp_cycle_check first (continuity between stages, body wholly inside the bay when parked, clearances), then
   run each stage in Vehicle Tracking. Service vehicles that do not park: ingress -> stand/turn -> egress.
   Reversing onto or off the public road is a finding, never a passing result.
   'Leaves in forward gear?' after a dead end is answered by sp_turn_search (Follow cannot do multi-point turns).
6a. Ramps (basement or podium access): plan swept paths do not prove a ramp. Check the long section with
   sp_ramp_section for every vehicle that uses the ramp: underside grounding at crests (breakover) and at
   the foot and head (approach/departure overhangs), and roof to soffit/beam/door-head headroom, in both
   directions. Floor and soffit come from the architect's section (trace it at true scale after
   vt_attach_pdf_page) or from a Civil 3D profile. Use the Vehicle Tracking profile's 'Min Body Ground
   Clearance' and 'Overall Body Height' for the vehicle; say so when an indicative value is used.
   Vehicle check: names such as 'P - Passenger Car' exist in several libraries (SANRAL 4.8 m, AASHTO 5.79 m), and
   the default-vehicle question shows only the name. Confirm the vehicle from its inserted profile. The Library
   Explorer's tree has no readable names: the server selects by keyboard type-ahead ({"tree_keys": path});
   check the result with c3d_dialog_image before Proceed. Simplest: have the person Make Default the TIA's
   car once, then every run answers Yes and the library never opens.
6b. Circulation arrows on every swept-path sheet: vt_circulation_arrows(kind='ingress'|'egress', guide_handle,
   stage_number=n) after each run - green for ingress, red for egress - carried into every figure.
7. Figures with c3d_export_png (the person's view is restored afterwards).
Never delete a good path to fix something cosmetic; fix it in place or note it. If Civil 3D is stuck in a
command (PAN, a prompt), c3d_clear_command cancels it from here: do not ask the person to press Esc.
"""

mcp = _Server("civil3d", instructions=INSTRUCTIONS)
C = Civil3D()
L = Launcher(C)
watchdog.start_sweeper()


async def run(fn, *a, answers: dict | None = None, **kw) -> Any:
    """Run a blocking Civil 3D step on the COM thread.

    A dialog that stops the step comes back as the 'dialog_pending' payload
    (whatever exception it caused on the way out), never as a hang.
    """
    def wrapped():
        C.answers = answers
        try:
            return fn(*a, **kw)
        finally:
            C.answers = None

    try:
        return await asyncio.to_thread(C.call, wrapped)
    except DialogPending as e:
        return e.payload
    except Exception as e:  # raw COM errors, timeouts: report, don't crash the client
        if C._dialog_open():
            payload = C.runner.pending_payload(C.runner.active)
            payload["interrupted_step"] = f"{type(e).__name__}: {e}"
            return payload
        if isinstance(e, (Civil3DError, DialogError)):
            out = {"error": str(e)}
            out.update(getattr(e, "details", None) or {})
            return out
        return {"error": f"{type(e).__name__}: {e}"}


def _r(x: float) -> float:
    return round(float(x), 3)


def _pts(pts) -> list[list[float]]:
    return [[_r(p[0]), _r(p[1])] for p in pts]


# =============================================================================
# CORE: session, drawing, commands
# =============================================================================
@mcp.tool()
async def c3d_status() -> dict:
    """Connection check: Civil 3D version, active drawing, whether it is a working copy, command state.
    Always answers within a few seconds: if another step is still running it reports that step instead."""
    try:
        out = await asyncio.to_thread(C.call, C.status, lock_timeout=3)
    except Civil3DError:
        out = C.quick_status()
    except Exception as e:
        out = C.quick_status()
        out["error"] = f"{type(e).__name__}: {e}"
    if L.state != "idle" or "No running Civil 3D" in str(out.get("reason", "")):
        out["launch"] = L.report()
    if out.get("busy"):
        try:
            b = await asyncio.to_thread(watchdog.blockers, getattr(C, "_hwnd", 0))
            if b["windows"]:
                out["blockers"] = [w["title"] or "(untitled)" for w in b["windows"]]
                out["modal"] = b["modal"]
                out["next"] = "Something is holding Civil 3D: c3d_blockers() reads it in full, c3d_screen() shows it."
        except Exception:
            pass
    return out


@mcp.tool()
async def c3d_blockers(act: str = "none", hwnd: int | None = None, click: str | None = None, reason: str = "") -> dict:
    """Every pop-up holding Civil 3D, read in full (title, text, buttons, inputs) and classed as safe_notice,
    notice, cancelable, protected or unknown - including WebView2 dialogs that screenshots can't see.
    act='safe' closes catalogued and plain OK notices; act='auto' also presses Cancel where offered.
    Protected and unknown pop-ups are never answered by act: answer one only on the person's word, with
    hwnd=<from this list>, click=<button name, or 'WM_CLOSE' for its X> and the reason."""
    try:
        if hwnd is not None and click:
            if not reason:
                return {"error": "Give the reason (whose decision, what was asked) for the log."}
            return await asyncio.to_thread(watchdog.click, int(hwnd), click, reason)
        if act not in ("none", "safe", "auto"):
            return {"error": "act is 'none', 'safe' or 'auto'."}
        return await asyncio.to_thread(watchdog.handle, act, getattr(C, "_hwnd", 0))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def c3d_screen():
    """Picture of the Civil 3D window as it is on the monitor, pop-ups included (also those computer-use
    screenshots miss), with other programs cropped away. Uses no COM, so it works while Civil 3D is blocked."""
    import datetime as _dt

    out = watchdog.LOG_DIR / "screens" / f"{_dt.datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        info = await asyncio.to_thread(watchdog.screen, out)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return [_Image(path=info["path"]), f"modal: {info['modal']}; pop-ups: {info['blockers'] or 'none'}"]


def _not_running(st: dict) -> bool:
    return "No running Civil 3D" in str(st.get("reason", ""))


@mcp.tool()
async def c3d_launch(folder: str | None = None, name: str | None = None, template: str | None = None,
                     wait_s: float = 40, end_hidden: bool = False) -> dict:
    """Open a fresh Civil 3D session. Starts Civil 3D 2027 Metric from its Start-menu shortcut if none is running
    (never a second one) and, given folder and name, saves a new working drawing <folder>/<name>.dwg (metres,
    dynamic input off, Arial), as c3d_new_drawing does. Starting takes 1-3 minutes and runs in the background:
    each call waits up to wait_s (max 45) and reports launch_state (starting / ready / failed). Call again until
    it is 'ready' and new_drawing is filled. If Civil 3D is already open, it just makes the drawing.
    launch_state 'blocked' means a hidden acad.exe left from an earlier session is in the way: ask the person,
    then call again with end_hidden=True to end it (only if it has no window) and start Civil 3D."""
    wait_s = max(0.0, min(float(wait_s), 45.0))
    req = {"folder": folder, "name": name, "template": template} if folder and name else None
    if L.state == "starting" or (L.state == "ready" and L.request and L.drawing is None):
        if req and not L.request:
            L.request = req
        return await asyncio.to_thread(L.wait, wait_s)
    if L.state == "ready" and L.request and req and L.request["folder"] == folder and L.request["name"] == name:
        return L.report()  # this drawing was already made by the launch; report it rather than make a second
    try:
        st = await asyncio.to_thread(C.call, C.status, lock_timeout=5)
    except Exception as e:
        st = {"reason": str(e)}
    if not _not_running(st):
        out = {"launch_state": "already_running", "civil3d": st}
        if req:
            out["new_drawing"] = await run(C.new_drawing, folder, name, template)
        else:
            out["next"] = "Civil 3D is already open. Start the working drawing with c3d_new_drawing(folder, name)."
        return out
    await asyncio.to_thread(L.start, folder, name, template, end_hidden)
    return await asyncio.to_thread(L.wait, wait_s)


@mcp.tool()
async def c3d_make_working_copy(folder: str | None = None, name: str | None = None) -> dict:
    """Save the active drawing as a working copy (always a .dwg) in a 'Claude outputs' folder beside it, or in
    `folder` (e.g. the job folder), and switch to it. The original file is not written. A never-saved drawing
    is saved straight into `folder` as `name`. Do this before any change."""
    return await run(C.make_working_copy, folder, name=name)


@mcp.tool()
async def c3d_new_drawing(folder: str, name: str, template: str | None = None) -> dict:
    """Start a new drawing, save it as <folder>/<name>.dwg and make it the active working copy: metres,
    dynamic input off, Standard text style in Arial. Use this instead of building on an unsaved drawing."""
    return await run(C.new_drawing, folder, name, template)


@mcp.tool()
async def c3d_clear_command() -> dict:
    """Cancel whatever command is running in Civil 3D (PAN, a waiting prompt, a half-finished command) by
    sending Esc through the window. Works while Civil 3D refuses COM calls, so the person never has to."""
    try:
        return await asyncio.to_thread(C.call, C.clear_command, lock_timeout=3)
    except Civil3DError:
        # Another step holds the COM thread (often the very command that is stuck): send Esc straight
        # through the window without waiting for it.
        return await asyncio.to_thread(C.escape_now)


@mcp.tool()
async def c3d_open_drawing(path: str, read_only: bool = True) -> dict:
    """Open (or activate) a DWG in the running Civil 3D. Opens read-only by default."""
    return await run(C.open_drawing, path, read_only)


@mcp.tool()
async def c3d_set_original_protection(enabled: bool) -> dict:
    """Turn the working-copy guard on or off. Only turn it off when the user explicitly asks to edit an original."""
    C.allow_original = not enabled
    return {"protection": enabled}


@mcp.tool()
async def c3d_command(command: str, wait: bool = True, timeout_s: float = 40, answers: dict | None = None) -> dict:
    """Send a command string to the Civil 3D command line, e.g. '_.ZOOM _E' or '(command "_.REGEN")'.
    Dialogs it opens are watched: confirmed catalogue dialogs are answered from `answers`
    ({catalogue_id: {"actions": [...]}}), anything else returns 'dialog_pending'.
    Waits up to timeout_s (max 40); a longer command returns 'running' (continue with c3d_run_status).
    Changing commands are blocked on non-working-copies."""
    def f():
        C.guard_write()
        return C.send(command, wait, timeout_s, label=command[:40])

    return await run(f, answers=answers)


@mcp.tool()
async def c3d_lisp(expression: str, timeout_s: float = 60) -> dict:
    """Evaluate one AutoLISP expression and return its printed value, e.g. '(getvar "CLAYER")' or
    '(length (vl-directory-files "C:/jobs" "*.dwg"))'. Quotes are fine. Blocked on non-working-copies,
    because LISP can change the drawing; use c3d_get_vars for read-only variable checks."""
    def f():
        C.guard_write()
        return C.lisp(expression, timeout_s)

    return await run(f)


@mcp.tool()
async def c3d_wait_idle(timeout_s: float = 30) -> dict:
    """Wait for the running command to end (max 40 s per call). Returns a waiting dialog's contents instead of hanging."""
    return await run(C.wait_idle, min(timeout_s, 40))


# =============================================================================
# CORE: runs and dialogs
# =============================================================================
@mcp.tool()
async def task_preflight(workflow: str = "vt_follow") -> dict:
    """Questions to ask the person ONCE before a task, plus the dialogs the task is known to meet and the
    answer each needs. Ask all questions together, then pass the answers to the run tool."""
    return C.runner.catalogue.preflight(workflow)


@mcp.tool()
async def c3d_run_status(wait_s: float = 25) -> dict:
    """State of the current command run: running, dialog_pending (with the dialog), waiting_at_prompt, finished
    (with results, dialogs answered, findings and the log file). Watches up to wait_s (max 40)."""
    return await run(C.runner.status, min(wait_s, 40))


@mcp.tool()
async def c3d_dialog_act(
    dialog_id: str,
    actions: list[dict],
    reason: str,
    learn: dict | None = None,
    finding: str | None = None,
    wait_s: float = 25,
) -> dict:
    """Answer the waiting dialog, then keep watching the run.
    actions, in order: {"click": "OK"} | {"select": {"label": "Direction", "value": "Forwards"}} (dropdown, or a
    radio button with value true) | {"check": {"label": "...", "value": false}} | {"type": {"label": "...", "value": "2.5"}}
    | {"tree": ["South African Design Vehicles", "SANRAL", "SU - Single Unit"]} | {"wait": 0.5}.
    Controls can also be named by id from the dialog listing: {"click": "c12"}.
    reason: why this answer, from the task (it goes in the run log).
    learn: on the first confirmed sighting, record the dialog, e.g. {"id": "vt.follow_settings", "description": "...",
    "kind": "per_task|per_run|fixed|judgement", "question": "...", "text_contains": "...", "workflows": ["vt_follow"],
    "fixed_actions": [...] (only for kind 'fixed', after the person confirmed the answer)}.
    finding: text to record as a finding of the task (e.g. a warning the dialog gave)."""
    return await run(C.runner.act, dialog_id, actions, reason, learn, finding, min(wait_s, 40))


@mcp.tool()
async def c3d_dialog_image(dialog_id: str):
    """Picture of the waiting dialog's window(s), drawn by the window itself (not a screenshot of the screen).
    Use only when part of the dialog has no readable text, e.g. the Vehicle Diagram."""
    try:
        paths = await asyncio.to_thread(C.runner.dialog_image, dialog_id)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return [_Image(path=str(p)) for p in paths]


@mcp.tool()
async def c3d_run_input(text: str, wait_s: float = 25) -> dict:
    """Type input at a command-line prompt the current run is waiting at (e.g. a point '12.5,40'). Never empty."""
    return await run(C.runner.send_input, text, min(wait_s, 40))


@mcp.tool()
async def c3d_run_cancel(force: bool = False) -> dict:
    """Cancel the current run (sends Esc twice through the window). A dialog that has already closed is forgotten.
    A dialog that is still open must be answered with c3d_dialog_act, unless force=True (Esc on the dialog too)."""
    return await run(C.runner.cancel, force)


@mcp.tool()
async def c3d_dialog_catalogue(entry_id: str | None = None) -> dict | list:
    """What the server has learned about dialogs: every entry, whether it is confirmed live, how often seen.
    With entry_id, the full entry (fingerprints, example contents, answer format)."""
    cat = C.runner.catalogue
    if entry_id:
        return cat.get(entry_id) or {"error": f"No catalogue entry '{entry_id}'."}
    return cat.summary()


@mcp.tool()
async def c3d_run_log(run_id: str | None = None, last_events: int = 60) -> dict:
    """The audit log of a run (default: the latest): every dialog seen and answered, input typed, finding.
    For the TIA's record of how swept paths were produced."""
    from .runner import LOG_DIR
    import json as _json

    files = sorted(LOG_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    if run_id:
        files = [f for f in files if f.stem == run_id]
    if not files:
        return {"error": "No run log found."}
    f = files[-1]
    rows = [_json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        r.pop("dialog", None)  # full dialog contents are in the file; keep the reply short
    return {"run_id": f.stem, "file": str(f), "events": rows[-last_events:]}


@mcp.tool()
async def c3d_knowledge() -> str:
    """Best-practice notes for this server: Vehicle Tracking settings, AutoCAD dialog variables, observed behaviour,
    and what to ask before a task. Read once per task."""
    f = HOME / "knowledge.md"
    text = f.read_text(encoding="utf-8") if f.exists() else "knowledge.md not found beside the catalogue."
    local = HOME / "knowledge.local.md"  # project-specific notes, kept out of the public repository
    if local.exists():
        text += "\n\n" + local.read_text(encoding="utf-8")
    return text


@mcp.tool()
async def c3d_get_vars(names: list[str]) -> dict:
    """Read system variables, e.g. ['INSUNITS','DYNMODE','CLAYER','LUNITS']."""
    def f():
        return {n: C.get_var(n) for n in names}

    return await run(f)


@mcp.tool()
async def c3d_set_var(name: str, value: Any) -> dict:
    """Set a system variable (ints, floats or strings)."""
    def f():
        C.set_var(name, value)
        return {name: C.get_var(name)}

    return await run(f)


@mcp.tool()
async def c3d_save() -> dict:
    """QSAVE the active working copy."""
    return await run(lambda: {"saved": C.qsave()})


@mcp.tool()
async def c3d_export_png(path: str, window: list[float] | None = None) -> dict:
    """Export a PNG of the view, or of a zoom window [x1, y1, x2, y2] in drawing metres, without dialogs."""
    return await run(C.png_out, path, tuple(window) if window else None)


# =============================================================================
# CORE: layers, entities, drawing
# =============================================================================
@mcp.tool()
async def c3d_layers(with_counts: bool = False) -> list | dict:
    """List layers (name, on, frozen, colour). with_counts adds model-space entity counts (slower on big drawings)."""
    return await run(lambda: dwg.layer_inventory(C) if with_counts else C.layers())


@mcp.tool()
async def c3d_query_entities(layer_pattern: str = "*", types: str | None = None, limit: int = 200) -> dict:
    """Find model-space entities by layer wildcard (e.g. 'A-WALL*,KERB*') and optional DXF types
    (e.g. 'LWPOLYLINE,LINE', 'AECC_ALIGNMENT', 'INSERT'). Returns handle, type, layer, bounding box."""
    def f():
        ents = C.select_on_layers(layer_pattern, types)
        rows = []
        for e in ents[:limit]:
            row = {"handle": e.Handle, "type": e.ObjectName, "layer": e.Layer}
            try:
                row["bbox"] = C.bbox(e)
            except Exception:
                pass
            rows.append(row)
        return {"total": len(ents), "returned": len(rows), "entities": rows}

    return await run(f)


@mcp.tool()
async def c3d_entity_info(handle: str, include_points: bool = True) -> dict:
    """Properties of one entity (type, layer, colour, length/area where available) and its 2D points."""
    def f():
        e = C.entity(handle)
        info: dict[str, Any] = {"handle": handle, "type": e.ObjectName, "layer": e.Layer}
        for prop in ("Color", "Linetype", "Length", "Area", "Closed", "Name", "Radius", "TextString", "Elevation"):
            try:
                v = getattr(e, prop)
                info[prop] = _r(v) if isinstance(v, float) else v
            except Exception:
                pass
        if include_points:
            parts = dwg.entity_points(C, e)
            info["parts"] = [{"kind": k, "closed": c, "points": _pts(p)} for p, c, k in parts]
        return info

    return await run(f)


@mcp.tool()
async def c3d_set_entity_property(handle: str, prop: str, value: Any) -> dict:
    """Set one COM property on an entity, e.g. ('1A2B', 'Layer', 'SP-SU'), ('1A2B', 'Color', 3)."""
    def f():
        C.guard_write()
        e = C.entity(handle)
        val = C.vdoubles(value) if isinstance(value, list) and all(isinstance(x, (int, float)) for x in value) else value
        C._retry(lambda: setattr(e, prop, val))
        return {"handle": handle, prop: getattr(e, prop)}

    return await run(f)


@mcp.tool()
async def c3d_draw_polyline(points: list[list[float]], layer: str, color: int | None = None, closed: bool = False) -> dict:
    """Draw a 2D polyline from [[x, y], ...] in drawing metres on a layer (created if missing; colour is ACI)."""
    return await run(lambda: {"handle": C.add_polyline(points, layer, color, closed)})


@mcp.tool()
async def c3d_draw_text(text: str, x: float, y: float, height: float = 0.5, layer: str = "ANNO-TEXT") -> dict:
    """Place single-line text."""
    return await run(lambda: {"handle": C.add_text(text, x, y, height, layer)})


@mcp.tool()
async def c3d_delete(handles: list[str]) -> dict:
    """Delete entities by handle (working copy only)."""
    def f():
        for h in handles:
            C.delete(h)
        return {"deleted": handles}

    return await run(f)


# =============================================================================
# CIVIL 3D OBJECTS
# =============================================================================
@mcp.tool()
async def civil_inventory() -> dict:
    """Counts and names of alignments, profiles, surfaces, corridors, COGO points, feature lines, pipe networks."""
    return await run(civ.inventory, C)


@mcp.tool()
async def civil_alignments() -> list | dict:
    """Alignments with length, start/end station and their profiles."""
    return await run(civ.alignment_summary, C)


@mcp.tool()
async def civil_point_at_station(alignment: str, station: float, offset: float = 0.0) -> dict:
    """Easting/northing at a station and offset (+ right of the alignment direction in Civil 3D)."""
    return await run(civ.point_at_station, C, alignment, station, offset)


@mcp.tool()
async def civil_station_offset(alignment: str, x: float, y: float) -> dict:
    """Station and offset of a point relative to an alignment."""
    return await run(civ.station_offset, C, alignment, x, y)


@mcp.tool()
async def civil_profile_elevations(alignment: str, profile: str, stations: list[float]) -> dict:
    """Profile elevations at stations (e.g. to read grades for sight distance)."""
    return await run(civ.profile_elevations, C, alignment, profile, stations)


@mcp.tool()
async def civil_surface_elevation(surface: str, points: list[list[float]]) -> dict:
    """Surface elevations at [[x, y], ...]."""
    return await run(civ.surface_elevation, C, surface, [tuple(p) for p in points])


@mcp.tool()
async def civil_alignment_from_polyline(polyline_handle: str, name: str, layer: str = "C-ROAD-ALIGN") -> dict:
    """Create a siteless alignment from a polyline with default styles."""
    return await run(civ.alignment_from_polyline, C, polyline_handle, name, layer)


# =============================================================================
# ROAD DESIGN PRODUCTION (v0.8): read how a data-shortcut road project is built
# =============================================================================
@mcp.tool()
async def road_drawing_report(parts: list[str] | None = None, detail: bool = True) -> dict:
    """How the active drawing is built, read-only. parts (default all): 'alignments' (data-reference state
    own/ref/stale/BROKEN, profiles, profile views, sample line groups with spacing and section views),
    'surfaces', 'corridors' (baselines, regions, assemblies, station counts), 'xrefs' (and whether found),
    'layouts' (plotter, ctb, paper, viewport scales, title-block attributes), 'types' (model-space counts).
    'needs_sync' lists data references that are stale or broken."""
    return await run(roads.drawing_report, C, parts, detail)


@mcp.tool()
async def road_report_file(path: str, parts: list[str] | None = None, detail: bool = True) -> dict:
    """Open a drawing read-only (unless it is already open) and report how it is built (as road_drawing_report).
    The drawing stays open: don't close it over COM (that crashed Civil 3D); restart Civil 3D between batches.
    One drawing at a time; a large corridor drawing can take 30-60 s to open."""
    return await run(roads.report_file, C, path, parts, detail)


@mcp.tool()
async def c3d_close_drawing(path: str, force: bool = False) -> dict:
    """Close an open drawing without saving. COM closes of data-shortcut drawings crashed Civil 3D four times,
    so without force=True this declines and explains: leave such drawings open and restart Civil 3D between
    batches. force=True only for a plain drawing with no data references or xrefs."""
    return await run(roads.close_drawing, C, path, force)


@mcp.tool()
async def road_sheets(attributes: bool = True) -> list | dict:
    """Every sheet (paper-space layout) of the active drawing: plotter, plot style, paper, viewport scales
    (paper mm per drawing metre and the scale that gives) and title-block attributes with the block handle."""
    return await run(lambda: roads.layouts(C.doc(), attributes))


@mcp.tool()
async def road_shortcut_project(folder: str) -> dict:
    """Read a Civil 3D data-shortcut project from disk (no Civil 3D needed): every shortcut by type, the source
    drawing each points to, how many shortcuts each source drawing publishes, and any source that is missing."""
    try:
        return await asyncio.to_thread(roads.shortcut_project, folder)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def c3d_pdf_page(pdf: str, page: int = 1, dpi: int = 60):
    """Look at one page of a PDF, e.g. an issued drawing sheet, as an image. Raise dpi to read small text."""
    try:
        path = await asyncio.to_thread(roads.pdf_page_png, pdf, page, dpi)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    return [_Image(path=path), path]


# =============================================================================
# ROAD PRODUCTION (v0.9): sync, rebuild, title blocks, plot, quantities, set-out
# =============================================================================
@mcp.tool()
async def road_sync_references() -> dict:
    """Synchronise every data reference in the active working drawing (staging copy), with the stale list before
    and after. A renamed source raises 'Object Name Change': that comes back for the person to decide."""
    return await run(prod.sync_references, C)


@mcp.tool()
async def road_rebuild_corridors(names: list[str] | None = None, only_out_of_date: bool = False) -> dict:
    """Rebuild corridors in the active working drawing (all, or `names`), one at a time, reporting OutOfDate
    before and after. Those that don't fit in this call come back in 'left'. Save afterwards with c3d_save."""
    return await run(prod.rebuild_corridors, C, names, only_out_of_date)


@mcp.tool()
async def road_set_titleblock(values: dict[str, str], layouts: list[str] | None = None, block: str | None = None,
                              save: bool = True) -> dict:
    """Set title-block attributes by tag, e.g. {'DESIGNED': 'AB', 'CHECKED': 'CD', 'DRAWN': 'EF', 'DATE': 'Oct-26',
    'REV.': '01'}, on every layout or the named ones. Only those tags change; the old and new values are returned.
    block: block name or wildcard (default any block carrying the tags). Working copies only; saves by default.
    Take the values from the client's initials rule (who designs, checks, approves and draws each discipline),
    never guessed."""
    def f():
        out = prod.set_titleblock(C, values, layouts, block)
        if save and out["count"]:
            out["saved"] = C.qsave()
        return out

    return await run(f)


@mcp.tool()
async def road_plot_sheets(folder: str | None = None, layouts: list[str] | None = None, names: list[str] | None = None,
                           regen: bool = True, overwrite: bool = False, allow_missing_ctb: bool = False,
                           install_ctb: str | None = None, check_only: bool = False) -> dict:
    """Plot layouts of the active drawing to PDF in `folder` (never a shared project folder), each with its own
    page set-up: all layouts in tab order, or `layouts` in the order given, named after the layout or `names`.
    check_only=True: just report each layout's device, paper and plot style, and whether the style is installed.
    install_ctb=<path to a .ctb>: copy a plot style into Civil 3D's plot-style folder first (a missing one plots
    in object colours without warning). A file that exists or is open in a PDF viewer is not overwritten: the sheet
    is plotted as '<name> v2.pdf'. Sheets that don't fit in one call come back in 'left'."""
    def f():
        out = {}
        if install_ctb:
            out["install_ctb"] = prod.install_ctb(C, install_ctb)
        if check_only:
            out.update(prod.plot_check(C, layouts))
            return out
        if not folder:
            raise Civil3DError("Give the output folder (in the staging folder, at the path the sheets belong).")
        out.update(prod.plot_layouts(C, folder, layouts, names, regen, overwrite, allow_missing_ctb))
        return out

    return await run(f)


@mcp.tool()
async def road_quantities(alignment: str, group: str | None = None, earthworks: str = "Earthworks",
                          ground: str | None = None, out_csv: str | None = None, detail: bool = False) -> dict:
    """Cut, fill and every material's volume (m3, average end area) for an alignment's sample line group, read from
    the material sections on each sample line. Read-only: needs Compute Materials to have been run. `ground` is
    part of the existing-ground section's name (default: the first surface section that isn't Top/Datum).
    out_csv writes the per-station areas (staging only); detail=True also returns them."""
    return await run(prod.quantities, C, alignment, group, earthworks, ground, out_csv, detail)


@mcp.tool()
async def road_stakeout(alignment: str, interval: float = 10.0, surface: str | None = None,
                        targets: dict[str, str] | None = None, out_csv: str | None = None) -> dict:
    """Stake-out table for a road: centre line and each target alignment (default every '<alignment>-...' one:
    edges, walkways, drains) at `interval` m and at the exact end station, with offset, E, N and level from
    `surface` (default '<alignment> Corridor - Top'). out_csv writes the full table (staging only)."""
    return await run(prod.stakeout, C, alignment, interval, surface, targets, out_csv)


@mcp.tool()
async def road_setout_data(alignment: str, profiles: list[str] | None = None, out_json: str | None = None) -> dict:
    """Setting-out data: every horizontal element of the alignment (type, stations, radius, PI and centre
    coordinates, tangent, external) and the PVIs of its design profiles (station, level, grades, curve length,
    K). out_json writes it (staging only)."""
    def f():
        out = prod.setout_data(C, alignment, profiles)
        if out_json:
            out = {"written": prod.write_json(out_json, out), "horizontal_elements": len(out["horizontal"]),
                   "profiles": {k: len(v) for k, v in out["profiles"].items()}}
        return out

    return await run(f)


@mcp.tool()
async def c3d_quit_civil3d(confirm: bool = False) -> dict:
    """Quit Civil 3D cleanly, to restart it between batches of heavy drawings. Refuses while any open drawing has
    unsaved changes. Without confirm=True it only lists what is open. Restart with c3d_launch."""
    return await run(prod.quit_civil3d, C, confirm)


@mcp.tool()
async def c3d_focus() -> dict:
    """Bring the running Civil 3D window to the front, e.g. before computer-use clicks. Never start or open
    Civil 3D from computer use: that launches a second copy."""
    try:
        return await asyncio.to_thread(prod.focus_window)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# =============================================================================
# SWEPT PATHS: Python engine
# =============================================================================
@mcp.tool()
async def sp_vehicles() -> dict:
    """Design vehicles known to the engine, with derived length, full-lock radius and steering angle."""
    return {k: v.summary() for k, v in all_vehicles().items()}


@mcp.tool()
async def sp_define_vehicle(
    name: str,
    description: str,
    width: float,
    front_overhang: float,
    wheelbase: float,
    rear_overhang: float,
    wall_to_wall_radius: float,
    articulated: bool = False,
    kingpin_offset: float = 0.0,
    trailer_front_overhang: float = 0.0,
    trailer_wheelbase: float = 0.0,
    trailer_rear_overhang: float = 0.0,
    source: str = "user",
) -> dict:
    """Add or override a vehicle (e.g. with values read off the Vehicle Tracking vehicle diagram).
    Saved to vehicles.json beside the server code."""
    v = Vehicle(
        name=name, description=description, width=width, front_overhang=front_overhang, wheelbase=wheelbase,
        rear_overhang=rear_overhang, wall_to_wall_radius=wall_to_wall_radius, articulated=articulated,
        kingpin_offset=kingpin_offset, trailer_front_overhang=trailer_front_overhang,
        trailer_wheelbase=trailer_wheelbase, trailer_rear_overhang=trailer_rear_overhang, source=source,
    )
    try:
        save_user_vehicle(v)
    except ValueError as e:
        return {"error": str(e)}
    return v.summary()


@mcp.tool()
async def sp_pixels_to_metres(points_px: list[list[float]], metres_per_pixel: float, image_height_px: int, origin_x: float = 0.0, origin_y: float = 0.0) -> dict:
    """Convert image pixel coordinates (origin top-left, y down) to drawing metres for an image attached
    with its bottom-left corner at (origin_x, origin_y). Metres per pixel = 25.4 / dpi x scale / 1000."""
    pts = [[origin_x + x * metres_per_pixel, origin_y + (image_height_px - y) * metres_per_pixel] for x, y in points_px]
    return {"points_m": _pts(pts)}


def _guide(points: list[list[float]] | None, handle: str | None, txt_file: str | None) -> list[tuple[float, float]]:
    if points:
        return [tuple(p) for p in points]
    if txt_file:
        rows = []
        for line in Path(txt_file).read_text().splitlines():
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                rows.append((float(parts[0]), float(parts[1])))
        return rows
    if handle:
        return C.call(dwg.polyline_points, C, handle)
    raise ValueError("Give the guide line as points, a polyline handle, or a txt file.")


@mcp.tool()
async def sp_build_guide(
    vertices: list[list[float]],
    radii: list[float] | float,
    build_up_m: float = 0.0,
    save_txt: str | None = None,
    draw_layer: str | None = None,
    color: int = 5,
) -> dict:
    """Guide line from corner vertices joined by circular fillets (radii >= vehicle min radius + 1 m).
    build_up_m smooths curvature in and out (2-4 m stops Follow overshooting full-lock arcs).
    Optionally writes 'x y' per line to save_txt and/or draws it on draw_layer."""
    try:
        pts = geo.fillet_path([tuple(v) for v in vertices], radii, build_up=build_up_m)
    except ValueError as e:
        return {"error": str(e)}
    out: dict[str, Any] = {"points": len(pts), "length_m": _r(sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))), "start": _pts([pts[0]])[0], "end": _pts([pts[-1]])[0]}
    shift = math.dist(pts[-1], tuple(vertices[-1]))
    if shift > 0.01:
        out["end_shift_m"] = _r(shift)
        out["end_note"] = "Build-up smoothing moved the end point; if it is a park position, nudge the last vertex back by this amount."
    if save_txt:
        Path(save_txt).parent.mkdir(parents=True, exist_ok=True)
        Path(save_txt).write_text("\n".join(f"{x:.4f} {y:.4f}" for x, y in pts))
        out["txt"] = save_txt
    if draw_layer:
        out["handle"] = await run(C.add_polyline, pts, draw_layer, color)
        out["pick_point"] = _pts([pts[len(pts) // 2]])[0]
    return out


def _obstacles(layers: str | None):
    return C.call(dwg.read_layers, C, layers) if layers else []


@mcp.tool()
async def sp_check_guide(
    vehicle: str,
    guide_points: list[list[float]] | None = None,
    guide_handle: str | None = None,
    guide_txt: str | None = None,
    direction: str = "forward",
    reference: str = "auto",
    obstacle_layers: str | None = None,
    required_clearance_m: float = 0.3,
) -> dict:
    """Kinematic check of a guide line before running Vehicle Tracking: steering against full lock,
    articulation, and clearance to linework on obstacle_layers (wildcards ok, e.g. 'A-WALL*,KERB,BAYS').
    reference='auto' (default: front axle forwards, as Vehicle Tracking Follow steers; rear axle in reverse),
    'rear_axle' or 'front_axle'."""
    try:
        v = get_vehicle(vehicle)
        guide = await asyncio.to_thread(_guide, guide_points, guide_handle, guide_txt)
        obs = await asyncio.to_thread(_obstacles, obstacle_layers)
        res = geo.simulate(v, guide, direction, reference)
    except (ValueError, KeyError, Civil3DError) as e:
        return {"error": str(e)}
    out = {
        "vehicle": v.name,
        "direction": direction,
        "path_length_m": _r(res.poses[-1].s),
        "max_steer_deg": res.max_steer_used_deg,
        "full_lock_deg": round(v.max_steer_deg, 1),
        "warnings": res.warnings,
    }
    if v.articulated:
        out["max_articulation_deg"] = res.max_artic_deg
    out["clearance"] = geo.clearance(v, res, obs, required_clearance_m)
    out["obstacles_read"] = len(obs)
    out["vehicle_source"] = v.source
    return out


def _poly_rings(g) -> list[list[tuple[float, float]]]:
    polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
    return [list(p.exterior.coords) for p in polys if isinstance(p, Polygon) and not p.is_empty]


@mcp.tool()
async def sp_draw_swept_path(
    vehicle: str,
    stage: str,
    guide_points: list[list[float]] | None = None,
    guide_handle: str | None = None,
    guide_txt: str | None = None,
    direction: str = "forward",
    reference: str = "auto",
    obstacle_layers: str | None = None,
    outline_interval_m: float = 4.0,
    hatch: bool = True,
    required_clearance_m: float = 0.3,
) -> dict:
    """Draw the engine's swept path into the working copy: envelope (hatched), wheel tracks, and vehicle
    outlines every outline_interval_m plus start and end, on layers SP-<vehicle>-<stage>-*. Also returns
    the clearance check. Use this to draw swept paths straight from DWG linework without Vehicle Tracking;
    label figures 'kinematic check' rather than 'Vehicle Tracking'."""
    try:
        v = get_vehicle(vehicle)
        guide = await asyncio.to_thread(_guide, guide_points, guide_handle, guide_txt)
        obs = await asyncio.to_thread(_obstacles, obstacle_layers)
        res = geo.simulate(v, guide, direction, reference)
    except (ValueError, KeyError, Civil3DError) as e:
        return {"error": str(e)}
    env = geo.envelope(v, res)
    wheels = geo.wheel_paths(v, res)
    outlines = geo.outlines_at(v, res, outline_interval_m)
    base = f"SP-{v.name}-{stage}"

    def draw():
        C.guard_write()
        handles: dict[str, list[str]] = {"envelope": [], "hatch": [], "wheels": [], "outlines": []}
        for ring in _poly_rings(env):
            h = C.add_polyline(ring, f"{base}-ENV", 1, closed=True)
            handles["envelope"].append(h)
            if hatch:
                try:
                    handles["hatch"].append(C.add_hatch(h, f"{base}-HATCH", 1, 70))
                except Exception:
                    pass
        for name, pts in wheels.items():
            handles["wheels"].append(C.add_polyline(pts[::3] + [pts[-1]], f"{base}-WHEELS", 8))
        for ring in outlines:
            handles["outlines"].append(C.add_polyline(ring, f"{base}-BODY", 7, closed=True))
        C.qsave()
        return handles

    drawn = await run(draw)
    return {
        "vehicle": v.name,
        "stage": stage,
        "envelope_area_m2": _r(env.area),
        "max_steer_deg": res.max_steer_used_deg,
        "warnings": res.warnings,
        "clearance": geo.clearance(v, res, obs, required_clearance_m),
        "drawn": drawn,
        "layers": f"{base}-ENV / -HATCH / -WHEELS / -BODY",
    }


@mcp.tool()
async def sp_turn_search(
    vehicle: str,
    start: list[float],
    obstacle_layers: str,
    required_clearance_m: float = 0.3,
    lateral_band: list[float] | None = None,
    target_heading_deg: float | None = None,
    max_forward_m: float = 25.0,
    draw_layer: str | None = None,
) -> dict:
    """Can this vehicle turn round here? Searches three-point and T-turns (forward, reverse, forward).
    start = [x, y, heading_deg] of the rear axle where the manoeuvre may begin (e.g. driving into the yard).
    lateral_band = [min, max] across the start heading for the finishing position (e.g. the aisle), optional.
    Vehicle Tracking Follow cannot do multi-point turns, so this is the test for 'leaves in forward gear'.
    Report it as a kinematic check. draw_layer draws the rear-axle path of each stage of the best sequence."""
    try:
        v = get_vehicle(vehicle)
        obs = await asyncio.to_thread(_obstacles, obstacle_layers)
        res = await asyncio.to_thread(
            geo.turn_search, v, (start[0], start[1], math.radians(start[2])), obs, required_clearance_m,
            None if target_heading_deg is None else math.radians(target_heading_deg), 15.0,
            tuple(lateral_band) if lateral_band else None, max_forward_m)
    except (ValueError, KeyError, Civil3DError) as e:
        return {"error": str(e)}
    if draw_layer and res.get("best"):
        hs = []
        for st, col in zip(res["best"]["stages"], (3, 1, 3)):
            pts = st["rear_axle_path"]
            if len(pts) >= 2:
                hs.append(await run(C.add_polyline, pts, draw_layer, col))
        res["drawn"] = hs
    res["obstacles_read"] = len(obs)
    return res



def _section_poly(points, handle, what):
    if points:
        pts = [(float(a), float(b)) for a, b in points]
    elif handle:
        pts = [tuple(p) for p in C.call(dwg.polyline_points, C, handle)]
    else:
        return None
    if len(pts) >= 2 and pts[0][0] > pts[-1][0]:
        pts = pts[::-1]  # drawn right to left
    return pts


@mcp.tool()
async def sp_ramp_section(
    vehicle: str,
    floor_points: list[list[float]] | None = None,
    floor_handle: str | None = None,
    soffit_points: list[list[float]] | None = None,
    soffit_handle: str | None = None,
    ground_clearance: float | None = None,
    overhang_clearance: float | None = None,
    body_height: float | None = None,
    required_underbody_m: float = 0.05,
    required_headroom_m: float = 0.10,
    orientations: str = "both",
    png_path: str | None = None,
    draw_layer: str | None = None,
    title: str | None = None,
) -> dict:
    """Ramp check in long section: can the vehicle drive along the ramp without grounding or hitting the soffit?
    Floor (and optional soffit: slab underside, beams, door head) as (chainage, level) points or a polyline
    drawn in section at true scale (x = chainage, y = level). Checks breakover at crests, front and rear
    overhangs at the foot and head, and roof headroom, with the vehicle facing both ways (in and out
    forwards). Side-profile values default to the Vehicle Tracking profile figures where known (SU) and to
    indicative values otherwise: pass the VT profile's Min Body Ground Clearance and Overall Body Height.
    png_path writes a colour section figure with a scale bar; draw_layer draws the vehicle at its critical
    positions into the drawing (same coordinates as the section)."""
    try:
        v = get_vehicle(vehicle)
        floor = await asyncio.to_thread(_section_poly, floor_points, floor_handle, "floor")
        if not floor:
            raise ValueError("Give the floor as points or a polyline handle.")
        soffit = await asyncio.to_thread(_section_poly, soffit_points, soffit_handle, "soffit")
        d = dict(sec.SIDE_DEFAULTS.get(v.name.upper(), sec.SIDE_DEFAULTS["P"] if v.length < 6.5 else sec.SIDE_DEFAULTS["SU"]))
        given = [x is not None for x in (ground_clearance, overhang_clearance, body_height)]
        side = sec.SideProfile(
            v.name, v.wheelbase, v.front_overhang, v.rear_overhang,
            ground_clearance if ground_clearance is not None else d["ground_clearance"],
            overhang_clearance if overhang_clearance is not None else (ground_clearance if ground_clearance is not None else d["overhang_clearance"]),
            body_height if body_height is not None else d["body_height"],
            "values supplied" if all(given) else (d["source"] if not any(given) else
                "partly supplied (" + ", ".join(f"{n} {x}" for n, x in zip(("ground clearance", "overhang clearance", "body height"),
                (ground_clearance, overhang_clearance, body_height)) if x is not None) + "); rest from " + d["source"]),
        )
        res = await asyncio.to_thread(sec.ramp_check, side, floor, soffit, 0.1, required_underbody_m,
                                      required_headroom_m, orientations)
        if png_path:
            res["png"] = await asyncio.to_thread(sec.render_png, png_path, side, floor, soffit, res,
                                                 title or f"Ramp check in section: {v.description}")
    except (ValueError, KeyError, Civil3DError) as e:
        return {"error": str(e)}
    if draw_layer:
        hs = []
        for key, col in (("underbody", 30), ("headroom", 1)):
            pose = res["_worst_poses"].get(key)
            if pose:
                hs.append(await run(C.add_polyline, [list(p) for p in sec.outline(side, *pose)], draw_layer, col))
        res["drawn"] = hs
    res.pop("_worst_poses", None)
    res["vehicle_plan_dimensions_source"] = v.source
    return res


@mcp.tool()
async def sp_cycle_check(
    vehicle: str,
    stages: list[dict],
    obstacle_layers: str | None = None,
    bay_points: list[list[float]] | None = None,
    bay_handle: str | None = None,
    park_stage: int | None = None,
    required_clearance_m: float = 0.3,
    joint_tolerance_m: float = 0.5,
) -> dict:
    """Full-cycle check for one vehicle: ingress -> park -> egress as one connected sequence.
    stages: in driving order, each {"name": "ingress", "guide_points": [[x, y], ...] or "guide_handle": "...",
    "direction": "forward" | "reverse"}. Checks each stage (steering, clearances), that each stage starts
    where the previous one ended (within joint_tolerance_m and 15 degrees), and, with a bay outline, that the
    body at the end of the park stage (default: the last stage before the final one) lies wholly inside the
    bay. Run this before the Vehicle Tracking stages; it does not replace them."""
    try:
        v = get_vehicle(vehicle)
        obs = await asyncio.to_thread(_obstacles, obstacle_layers)
        results, prev_end = [], None
        joints_ok = True
        for i, st in enumerate(stages):
            guide = await asyncio.to_thread(_guide, st.get("guide_points"), st.get("guide_handle"), st.get("guide_txt"))
            direction = st.get("direction", "forward")
            r = geo.simulate(v, guide, direction, "auto")
            c = geo.clearance(v, r, obs, required_clearance_m)
            start, end = r.poses[0], r.poses[-1]
            rec = {"stage": st.get("name", f"stage {i + 1}"), "direction": direction,
                   "length_m": _r(end.s), "max_steer_deg": r.max_steer_used_deg, "warnings": r.warnings,
                   "clearance": c}
            if prev_end is not None:
                gap = math.hypot(start.x - prev_end.x, start.y - prev_end.y)
                dth = abs((math.degrees(start.theta - prev_end.theta) + 180) % 360 - 180)
                ok = gap <= joint_tolerance_m and dth <= 15
                joints_ok &= ok
                rec["joint_with_previous"] = {"gap_m": _r(gap), "heading_change_deg": round(dth, 1), "ok": ok}
            results.append((rec, r))
            prev_end = end
        out = {"vehicle": v.name, "stages": [x[0] for x in results], "stages_connected": joints_ok}
        bay = await asyncio.to_thread(_section_poly, bay_points, bay_handle, "bay") if (bay_points or bay_handle) else None
        if bay:
            k = park_stage if park_stage is not None else max(0, len(stages) - 2)
            body = geo.bodies(v, results[k][1].poses[-1])[0]
            bp = Polygon(bay)
            outside = body.difference(bp)
            out["parked"] = {"stage": results[k][0]["stage"], "inside_bay": outside.area < 0.01,
                             "area_outside_m2": round(outside.area, 3),
                             "max_distance_outside_m": round(max((bp.exterior.distance(Point(p)) for p in body.exterior.coords
                                                                  if not bp.buffer(1e-6).contains(Point(p))), default=0.0), 3)}
        all_clear = all(x[0]["clearance"].get("pass", True) and not x[0]["warnings"] for x in results)
        out["passes"] = joints_ok and all_clear and out.get("parked", {}).get("inside_bay", True)
        out["obstacles_read"] = len(obs)
        out["note"] = "Kinematic pre-check. Report the Vehicle Tracking runs of the same stages as the result."
        return out
    except (ValueError, KeyError, Civil3DError) as e:
        return {"error": str(e)}

@mcp.tool()
async def sp_parked_vehicles(bays: list[list[float]], vehicle: str = "P", layer: str = "SP-OBS-PARKED", color: int = 30) -> dict:
    """Draw a parked vehicle outline centred in each bay, along the bay's long side, as an obstacle for swept-path
    checks. bays: [[x1, y1, x2, y2], ...] axis-aligned bay rectangles in metres, or [[x1, y1, x2, y2, x3, y3, x4, y4]]
    for a rotated bay given by its four corners."""
    try:
        v = get_vehicle(vehicle)
    except KeyError as e:
        return {"error": str(e)}
    L, W = v.length, v.width
    out = []
    for b in bays:
        if len(b) == 4:
            x1, y1, x2, y2 = b
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        elif len(b) == 8:
            corners = [(b[i], b[i + 1]) for i in range(0, 8, 2)]
        else:
            return {"error": f"bay {b} must have 4 or 8 numbers"}
        cx = sum(p[0] for p in corners) / 4
        cy = sum(p[1] for p in corners) / 4
        e1 = (corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
        e2 = (corners[3][0] - corners[0][0], corners[3][1] - corners[0][1])
        long_, short = (e1, e2) if math.hypot(*e1) >= math.hypot(*e2) else (e2, e1)
        bay_len, bay_w = math.hypot(*long_), math.hypot(*short)
        ux, uy = long_[0] / bay_len, long_[1] / bay_len
        nx, ny = -uy, ux
        ring = [(cx + sx * L / 2 * ux + sy * W / 2 * nx, cy + sx * L / 2 * uy + sy * W / 2 * ny) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        h = await run(C.add_polyline, ring, layer, color, True)
        out.append({"handle": h, "bay_length_m": _r(bay_len), "bay_width_m": _r(bay_w), "fits": bay_len >= L and bay_w >= W})
    return {"vehicle": v.name, "drawn": out, "note": "Assumed parked vehicles, centred in each bay."}


@mcp.tool()
async def vt_pdf_scale(pdf: str, page: int, p1: list[float], p2: list[float], metres: float) -> dict:
    """Scale of a vector PDF sheet from a dimensioned line: p1, p2 in PDF points as the page is DISPLAYED
    (rotation applied), and the real length in metres. Returns metres per PDF point for vt_attach_pdf_page."""
    d = math.dist(p1, p2)
    if d <= 0:
        return {"error": "p1 and p2 are the same point"}
    return {"metres_per_point": metres / d, "check": f"{d:.1f} pt = {metres} m"}


@mcp.tool()
async def vt_attach_pdf_page(
    pdf: str,
    page: int,
    metres_per_point: float,
    origin_point: list[float],
    clip: list[float] | None = None,
    dpi: int = 300,
    greyscale: bool = True,
    lighten: float = 0.75,
    out_png: str | None = None,
) -> dict:
    """Render one page of the architect's PDF to a raster and attach it at true scale, in one step.
    page is 1-based. Coordinates are PDF points on the page as displayed (rotation applied).
    origin_point: the PDF point that becomes drawing (0, 0), e.g. the plot's bottom-left corner.
    clip: [x0, y0, x1, y1] part of the page to keep (default whole page). greyscale + lighten make the backdrop
    pale grey so coloured swept paths stand out. The PNG is saved beside the PDF (or out_png)."""
    def f():
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            try:
                import fitz  # type: ignore
            except ImportError:
                raise Civil3DError(f"PyMuPDF is not installed for the server's Python. Run: \"{sys.executable}\" -m pip install pymupdf  "
                                  "then restart Claude Desktop. Meanwhile render the page elsewhere and use vt_attach_image.")
        from PIL import Image

        doc = fitz.open(pdf)
        pg = doc[page - 1]
        pm = pg.get_pixmap(dpi=dpi)  # honours the page rotation
        png = out_png or str(Path(pdf).with_name(f"{Path(pdf).stem}_p{page}_backdrop.png"))
        tmp = png + ".full.png"
        pm.save(tmp)
        im = Image.open(tmp)
        s = dpi / 72.0
        x0, y0, x1, y1 = clip or [0, 0, pg.rect.width, pg.rect.height]
        im = im.crop((round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s)))
        if greyscale:
            im = im.convert("L").point(lambda v: 255 - int((255 - v) * lighten))
        im.save(png, optimize=True)
        try:
            os.remove(tmp)
        except Exception:
            pass
        mpp = metres_per_point / s
        ins_x = (x0 - origin_point[0]) * metres_per_point
        ins_y = (origin_point[1] - y1) * metres_per_point
        res = vt.attach_image(C, png, mpp, ins_x, ins_y)
        res.update({"png": png, "insert": [_r(ins_x), _r(ins_y)], "origin_point": origin_point})
        return res

    return await run(f)


# =============================================================================
# VEHICLE TRACKING
# =============================================================================
@mcp.tool()
async def vt_prepare_drawing() -> dict:
    """Set DYNMODE 0 (typed points arrive absolute) and INSUNITS 6 (metres)."""
    return await run(vt.prepare_drawing, C)


@mcp.tool()
async def vt_attach_image(path: str, metres_per_pixel: float, insert_x: float = 0.0, insert_y: float = 0.0) -> dict:
    """Attach a raster site plan at true scale, bottom-left at the insertion point."""
    return await run(vt.attach_image, C, path, metres_per_pixel, insert_x, insert_y)


@mcp.tool()
async def vt_list_paths() -> dict:
    """Vehicle Tracking paths in the drawing (layers ATR*), with handles and bounding boxes."""
    return {"paths": await run(vt.list_paths, C)}


@mcp.tool()
async def vt_follow_run(
    guide_handle: str | None = None,
    pick_x: float | None = None,
    pick_y: float | None = None,
    direction: str = "Forwards",
    vehicle: str | None = None,
    vehicle_path: list[str] | None = None,
    speed_policy: str = "ask",
    preselect_path: str | None = None,
    answers: dict | None = None,
    save: bool = True,
    isolate: bool = True,
) -> dict:
    """Drive a Vehicle Tracking Follow run on a guide line, with no clicks in Civil 3D.
    guide_handle (preferred): the guide polyline; the pick point is chosen on it. Or give pick_x/pick_y.
    The server isolates the guide (locks image/obstacle layers, hides other paths and guides), checks the pick
    hits only the guide, types the pick into Civil 3D's command line through the window when Follow asks
    'select object to Follow', then restores the layers and reports the new path (or that none was made).
    Dialogs are read and answered without the screen:
      direction: 'Forwards' or 'Reverse' (Follow Settings).
      vehicle: the vehicle's name as Vehicle Tracking shows it, e.g. 'SU - Single Unit'. The default-vehicle
        question is answered Yes if it names this vehicle, otherwise No, and vehicle_path is selected in the library.
      vehicle_path: library path for the Library Explorer, e.g. ['South African Design Vehicles','SANRAL','SU - Single Unit'].
      speed_policy: 'ask' (default: the 'reduce speed?' question comes back to Claude), 'accept_and_record', or
        'stop_and_report'. Either way it is recorded as a finding.
      preselect_path: handle of an earlier path driven with the wanted vehicle (Follow then reuses it).
      answers: extra or overriding dialog answers, keyed by catalogue id.
    Only confirmed catalogue dialogs are answered automatically; others return 'dialog_pending'.
    Returns 'finished' with the new path(s), 'dialog_pending', or 'running' (continue with c3d_run_status)."""
    return await run(vt.follow_run, C, pick_x, pick_y, direction, vehicle, vehicle_path, speed_policy, preselect_path, answers, save,
                     guide_handle=guide_handle, isolate=isolate)


@mcp.tool()
async def vt_vehicle_library(library: str | list[str] | None = None, refresh: bool = False, find: str | None = None) -> dict:
    """The Vehicle Tracking vehicle catalogue, read from the Vehicle Library Explorer without the screen.
    library=None: the libraries and their groups. library='South African Design Vehicles' (or a path list):
    every vehicle under it, as library paths ready for vt_follow_run(vehicle_path=...). Results are cached;
    refresh=True reads Civil 3D again. find='refuse' searches the cache by vehicle name.
    The TIA's design-vehicle table decides WHICH vehicles to run; this only finds them."""
    if find:
        return {"matches": vt.find_vehicle(find), "note": "From the cache; read the library first if empty."}
    cache = vt.library_cache()
    key = None if library is None else " > ".join([library] if isinstance(library, str) else library)
    if not refresh:
        if key is None and cache.get("top"):
            return {"cached": cache.get("read_at"), "tree": cache["top"]}
        if key and key in cache.get("libraries", {}):
            tree = cache["libraries"][key]
            return {"cached": cache.get("read_at"), "tree": tree, "vehicle_paths": vt._flatten(tree)}
    return await run(vt.read_library, C, library)


@mcp.tool()
async def vt_insert_profile(path_handle: str, x: float | None = None, y: float | None = None, force: bool = False) -> dict:
    """Insert the dimensioned vehicle profile for a path, once per path (a second insert would duplicate it).
    Without x, y it is placed in clear space to the right of the attached plan, 22 m along per profile."""
    return await run(vt.insert_profile, C, path_handle, x, y, force)


@mcp.tool()
async def vt_check_path(path_handle: str, obstacle_layers: str, required_clearance_m: float = 0.3) -> dict:
    """Clearance of what Vehicle Tracking actually drew (exploded copy of the path) to obstacle layers."""
    def f():
        obs = dwg.read_layers(C, obstacle_layers)
        return vt.check_vt_path(C, path_handle, obs, required_clearance_m)

    return await run(f)


def _draw_forms(forms: list[str]) -> int:
    """Send entmake forms to Civil 3D in a few (load ...) batches. Runs on the COM thread."""
    made = 0
    for expr in circ.batch(forms):
        res = C.lisp(expr, 40)
        if not res.get("ok"):
            raise Civil3DError(f"Drawing batch failed: {res.get('error') or res.get('hint')}")
        try:
            made += int(float(res["result"]))
        except Exception:
            pass
    return made


@mcp.tool()
async def c3d_draw_polylines(polylines: list[dict]) -> dict:
    """Draw many polylines in one go: [{"points": [[x, y], ...], "layer": "OBS-GROUND", "color": 8, "closed": false}, ...].
    Built as entmake data and loaded from a file in a few batches, so hundreds of walls, columns or bay lines
    go in with one call (pasting them through LISP or one COM call each is what stalled the server on a TIA job).
    Layers are created if missing (colour from the first polyline on each layer)."""
    def f():
        C.guard_write()
        forms, layers = [], {}
        for pl in polylines:
            pts = [(float(p[0]), float(p[1])) for p in pl["points"]]
            if len(pts) < 2:
                continue
            layers.setdefault(pl["layer"], pl.get("color"))
            forms.append(circ.lisp_polyline(pts, pl["layer"], pl.get("color"), bool(pl.get("closed"))))
        for lay, col in layers.items():
            C.ensure_layer(lay, col)
        return {"drawn": _draw_forms(forms), "layers": sorted(layers)}

    return await run(f)


@mcp.tool()
async def c3d_import_dxf(path: str, x: float = 0.0, y: float = 0.0, scale: float = 1.0, explode: bool = True) -> dict:
    """Bring a DXF or DWG (e.g. traced obstacle linework written with ezdxf) into the drawing at (x, y), keeping its
    layers, and explode it so its entities are individual objects. Returns the entity count on each new layer.
    The quickest way to put thousands of lines into the drawing (20 Oct 2026: 2,448 polylines in one call)."""
    def f():
        C.guard_write()
        p = str(Path(path)).replace("\\", "/")
        if not Path(path).exists():
            raise Civil3DError(f"File not found: {path}")
        before = {l["name"] for l in C.layers()}
        C.send(f'(command "_.-INSERT" "{p}" "{x},{y}" "{scale}" "{scale}" "0")', wait=True, timeout=40, label="import dxf")
        if explode:
            C.lisp('(progn (setvar "QAFLAGS" 0) (command "_.EXPLODE" (entlast)) T)', 40)
        new = sorted({l["name"] for l in C.layers()} - before)
        counts = {}
        for lay in new:
            res = C.lisp(f'(if (setq mcp:s (ssget "_X" (list (cons 8 "{lay}")))) (sslength mcp:s) 0)', 20)
            counts[lay] = int(res.get("result") or 0) if res.get("ok") else None
        return {"inserted": path, "new_layers": counts}

    return await run(f)


@mcp.tool()
async def vt_circulation_arrows(
    kind: str = "ingress",
    guide_handle: str | None = None,
    guide_points: list[list[float]] | None = None,
    spacing_m: float = 5.0,
    size_m: float = 1.2,
    reverse: bool = False,
    stage_number: int | None = None,
    layer: str | None = None,
    color: int | None = None,
) -> dict:
    """Direction arrows along a run, green for ingress (layer CIRC-IN) and red for egress (CIRC-OUT), as filled
    triangles every spacing_m along the guide line (or the points given), pointing the way the vehicle travels
    (reverse=True if the line is drawn the other way). stage_number puts a numbered circle at the start of the run.
    Draw them for every run of a swept-path sheet: they show the order of movement and give the developer the
    basis of the signed circulation. kind: 'ingress', 'egress' (or 'in'/'out')."""
    k = kind.lower()
    style = circ.INGRESS if k in ("ingress", "in", "entry") else circ.EGRESS if k in ("egress", "out", "exit") else None
    if style is None:
        return {"error": "kind must be 'ingress' or 'egress'."}
    lay, col = layer or style["layer"], color if color is not None else style["color"]

    try:
        pts = await asyncio.to_thread(_guide, guide_points, guide_handle, None)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    def f():
        C.guard_write()
        tris = circ.arrows_along(pts, spacing_m, size_m, reverse=reverse)
        forms = circ.lisp_solids(tris, lay, col)
        if stage_number is not None:
            p0 = pts[-1] if reverse else pts[0]
            C.ensure_layer(circ.STAGE["layer"], circ.STAGE["color"])
            forms += circ.lisp_stage_label(p0[0], p0[1] + size_m * 1.6, stage_number, size_m * 0.9, circ.STAGE["layer"], circ.STAGE["color"])
        C.ensure_layer(lay, col)
        return {"arrows": len(tris), "drawn": _draw_forms(forms), "layer": lay, "color": col}

    return await run(f)


@mcp.tool()
async def vt_annotate_png(png: str, metres_per_pixel: float, north_deg: float = 0.0, out: str | None = None) -> dict:
    """Crop the dark CAD background off an exported PNG and add a scale bar and north arrow.
    metres_per_pixel = zoom window width / exported image width."""
    return await asyncio.to_thread(vt.annotate_png, png, metres_per_pixel, north_deg, out)


# =============================================================================
# JUNCTIONS AND ROADS
# =============================================================================
@mcp.tool()
async def jn_kerb_return(
    edge_a: list[list[float]],
    edge_b: list[list[float]],
    radius: float = 10.0,
    three_centred: list[float] | None = None,
    draw_layer: str | None = None,
) -> dict:
    """Kerb return between two kerb lines. edge_a = [far point, point near corner] along the approach;
    edge_b = [point near corner, far point] along the exit. Simple radius, or three_centred=[R1,R2,R3]
    (e.g. [36,12,36]) with a 20/60/20 % deflection split."""
    try:
        res = geo.kerb_return(tuple(map(tuple, edge_a)), tuple(map(tuple, edge_b)), radius, tuple(three_centred) if three_centred else None)
    except ValueError as e:
        return {"error": str(e)}
    pts = res.pop("points")
    if draw_layer:
        res["handle"] = await run(C.add_polyline, pts, draw_layer, 3)
    res["points"] = _pts(pts[::4] + [pts[-1]])
    return res


@mcp.tool()
async def jn_turn_test(
    vehicle: str,
    entry_point: list[float],
    entry_heading_deg: float,
    exit_heading_deg: float,
    radius: float,
    lead_in_m: float = 15.0,
    lead_out_m: float = 15.0,
    obstacle_layers: str | None = None,
    draw: bool = False,
    stage: str = "turn",
) -> dict:
    """Test a junction turn: straight lead-in from entry_point, circular turn of `radius` (rear axle),
    straight lead-out. Checks steering and clearance to kerbs/islands on obstacle_layers; draw=True
    draws the envelope like sp_draw_swept_path. Left-hand traffic: a left turn is the tight one."""
    guide = geo.turn_guide(tuple(entry_point), entry_heading_deg, exit_heading_deg, radius, lead_in_m, lead_out_m)
    guide_pts = _pts(guide)
    if draw:
        return await sp_draw_swept_path(vehicle, stage, guide_points=guide_pts, obstacle_layers=obstacle_layers, reference="rear_axle")
    return await sp_check_guide(vehicle, guide_points=guide_pts, obstacle_layers=obstacle_layers, reference="rear_axle")


@mcp.tool()
async def jn_taper(start: list[float], direction_deg: float, length_m: float, offset_m: float, draw_layer: str | None = None) -> dict:
    """Straight taper for a flare, lane gain or merge: lateral offset (+ left) over length."""
    pts = geo.taper(tuple(start), direction_deg, length_m, offset_m)
    out: dict[str, Any] = {"points": _pts(pts), "ratio": f"1:{length_m / abs(offset_m):.1f}" if offset_m else None}
    if draw_layer:
        out["handle"] = await run(C.add_polyline, pts, draw_layer, 3)
    return out


@mcp.tool()
async def rd_stopping_sight_distance(speed_kmh: float, grade_pct: float = 0.0, reaction_s: float = 2.5, decel_ms2: float = 3.4) -> dict:
    """Stopping sight distance (AASHTO 2018 form). Compare with the MoWT Road Design Manual table for the design speed."""
    return {"ssd_m": _r(geo.stopping_sight_distance(speed_kmh, grade_pct, reaction_s, decel_ms2)), "inputs": locals()}


@mcp.tool()
async def rd_visibility_splay(
    access_point: list[float],
    road_heading_deg: float,
    x_m: float,
    y_left_m: float,
    y_right_m: float,
    draw_layer: str | None = None,
) -> dict:
    """Visibility splay for an access: eye set back x_m from the kerb, sight lines y_left/y_right along it.
    Optionally draws the splay triangle; check nothing on site layers crosses it."""
    res = geo.visibility_splay(tuple(access_point), road_heading_deg, x_m, y_left_m, y_right_m)
    if draw_layer:
        res["handle"] = await run(C.add_polyline, res["polygon"], draw_layer, 2, True)
    for k in ("eye_point",):
        res[k] = _pts([res[k]])[0]
    for k in ("splay_left", "splay_right", "polygon"):
        res[k] = _pts(res[k])
    return res


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
