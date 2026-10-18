"""Civil 3D MCP server.

Core tools give general access to a running Civil 3D session over COM.
Domain modules (swept paths, Vehicle Tracking, junctions, roads) build on them.

Run:  python -m civil3d_mcp.server
"""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image as _Image
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image as _Image

from shapely.geometry import MultiPolygon, Point, Polygon

from . import civil as civ
from . import dwg
from . import geometry as geo
from . import section as sec
from . import vt
from .catalogue import HOME
from .com_bridge import Civil3D, Civil3DError
from .dialogs import DialogError
from .runner import DialogPending
from .vehicles import Vehicle, all_vehicles, get_vehicle, save_user_vehicle

INSTRUCTIONS = """
Civil 3D over COM, for transport engineering work (TIAs, swept paths, junctions, road design). Server v0.4.

Ground rules:
- Never edit the user's original drawing. Call c3d_make_working_copy first; drawing-changing tools refuse otherwise.
- Never send an empty command (Enter repeats the last command). c3d_command refuses it.
- Attach to the running Civil 3D only; never start a second instance.
- After each successful step that changes the drawing, save, so finished work is never lost.
- Units are metres (INSUNITS 6). Traffic drives on the left in Uganda.

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
- A run that is still going returns 'running': continue with c3d_run_status. Tool calls never block > 40 s.
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
3. Obstacles as closed polylines on SP-OBS-* layers; sp_parked_vehicles for occupied bays.
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
7. Figures with c3d_export_png (the person's view is restored afterwards).
Never delete a good path to fix something cosmetic; fix it in place or note it. If Civil 3D is stuck in a
command (PAN, a prompt), c3d_clear_command cancels it from here: do not ask the person to press Esc.
"""

mcp = _Server("civil3d", instructions=INSTRUCTIONS)
C = Civil3D()


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
            return {"error": str(e)}
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
    """Connection check: Civil 3D version, active drawing, whether it is a working copy, command state."""
    return await run(C.status)


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
    return await run(C.clear_command)


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
async def c3d_run_cancel() -> dict:
    """Cancel a run waiting at a command-line prompt (sends Esc twice). A waiting dialog must be answered instead."""
    return await run(C.runner.cancel)


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
    return f.read_text(encoding="utf-8") if f.exists() else "knowledge.md not found beside the catalogue."


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
                raise Civil3DError("PyMuPDF is not installed for the server's Python: run 'pip install pymupdf' and restart the server.")
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
