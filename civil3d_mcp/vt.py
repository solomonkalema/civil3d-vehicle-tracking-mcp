"""Autodesk Vehicle Tracking helpers.

Vehicle Tracking has no public API. These helpers drive its commands through
COM. Its dialogs are read and answered through the runner (runner.py): known,
confirmed dialogs are answered from the task's settings, everything else comes
back to Claude with its full contents. No screen access is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

from shapely.geometry import LineString

from .com_bridge import Civil3D, Civil3DError
from .dwg import entity_points
from .geometry import Obstacle

PATH_LAYERS = "ATR*"


def list_paths(c3d: Civil3D) -> list[dict]:
    rows = []
    for e in c3d.select_on_layers(PATH_LAYERS):
        r = c3d._retry
        kind = r(lambda e=e: e.ObjectName)
        if "Vehicle" not in kind and "Path" not in kind and "ATR" not in kind.upper():
            continue
        row = {"handle": r(lambda e=e: e.Handle), "layer": r(lambda e=e: e.Layer), "object": kind}
        try:
            row["bbox"] = c3d.bbox(e)
        except Exception:
            pass
        rows.append(row)
    return rows


def prepare_drawing(c3d: Civil3D) -> dict:
    """Settings the VT workflow needs: no dynamic input, metres."""
    c3d.set_var("DYNMODE", 0)
    c3d.set_var("INSUNITS", 6)
    return {"DYNMODE": 0, "INSUNITS": 6}


def attach_image(c3d: Civil3D, path: str, metres_per_pixel: float, insert_x: float = 0.0, insert_y: float = 0.0, layer: str = "LAYOUT-IMAGE") -> dict:
    """Attach a raster site layout at true scale (bottom-left at the insertion point)."""
    from PIL import Image  # local import: only needed here

    c3d.guard_write()
    with Image.open(path) as im:
        w_px, h_px = im.size
    c3d.ensure_layer(layer)
    img = c3d._retry(lambda: c3d.ms().AddRaster(path, c3d.vpt(insert_x, insert_y), 1.0, 0.0))
    c3d._retry(lambda: setattr(img, "ImageWidth", w_px * metres_per_pixel))
    c3d._retry(lambda: setattr(img, "ImageHeight", h_px * metres_per_pixel))
    c3d._retry(lambda: setattr(img, "Layer", layer))
    return {
        "handle": c3d._retry(lambda: img.Handle),
        "pixels": [w_px, h_px],
        "size_m": [round(w_px * metres_per_pixel, 3), round(h_px * metres_per_pixel, 3)],
        "metres_per_pixel": metres_per_pixel,
        "check": "Measure one dimensioned line on the plan before drawing guide lines.",
    }


def build_answers(
    direction: str = "Forwards",
    position: str = "Automatic",
    vehicle: str | None = None,
    vehicle_path: list[str] | None = None,
    speed_policy: str = "ask",
    extra: dict | None = None,
) -> dict:
    """Turn a task's settings into answers for the catalogue's Vehicle Tracking dialogs.

    The server only uses an answer when the dialog is a confirmed, unchanged
    catalogue entry. Anything else still comes back to Claude.
    """
    a: dict = {
        "vt.follow_settings": {"actions": [
            {"select": {"label": "Direction", "value": direction}},
            {"select": {"label": "Position", "value": position}},
            {"click": "OK"},
        ]},
    }
    if vehicle:
        # Yes only if the default vehicle named in the dialog is the task's vehicle
        a["vt.default_vehicle"] = {"when_text_matches": re.escape(vehicle), "actions": [{"click": "Yes"}]}
    if vehicle_path:
        a["vt.vehicle_library"] = {"actions": [{"tree": list(vehicle_path)}, {"click": "Proceed"}]}
    if speed_policy == "accept_and_record":
        a["vt.speed_reduction"] = {"actions": [{"click": "Yes"}],
                                   "record_as_finding": "Follow could not track the guide line at design speed; Vehicle Tracking reduced the speed. Check the run speed and consider a steering build-up or a wider radius."}
    elif speed_policy == "stop_and_report":
        a["vt.speed_reduction"] = {"actions": [{"click": "No"}],
                                   "record_as_finding": "Follow could not track the guide line at design speed; the run was stopped as the task asked."}
    elif speed_policy != "ask":
        raise ValueError("speed_policy must be 'ask', 'accept_and_record' or 'stop_and_report'.")
    for k, v in (extra or {}).items():
        a[k] = v
    return a


def follow_run(
    c3d: Civil3D,
    pick_x: float,
    pick_y: float,
    direction: str = "Forwards",
    vehicle: str | None = None,
    vehicle_path: list[str] | None = None,
    speed_policy: str = "ask",
    preselect_path: str | None = None,
    answers: dict | None = None,
    save: bool = True,
    budget_s: float = 30.0,
) -> dict:
    """Run DRIVEFOLLOW on the guide line through (pick_x, pick_y) with dialogs handled by the runner.

    Returns 'finished' (with the new path), or 'dialog_pending' with the dialog's
    full contents for Claude to decide, or 'running' (continue with run_status).
    """
    c3d.guard_write()
    if c3d.runner.busy():
        raise Civil3DError(f"Run {c3d.runner.active.id} is still {c3d.runner.active.state}; finish it first.")
    st = c3d.cmdactive()
    if st["value"]:
        raise Civil3DError(f"A command is already running ({', '.join(st['active'])}). Finish it first.")
    before = {p["handle"] for p in list_paths(c3d)}
    if preselect_path:
        c3d.lisp(f'(sssetfirst nil (ssadd (handent "{preselect_path}")))')
    # Zoom in on the pick so the typed point lands on the guide line, not a neighbour.
    c3d.send(f'(command "_.ZOOM" "_C" "{pick_x:.3f},{pick_y:.3f}" 30)')

    def after() -> dict:
        new = [p for p in list_paths(c3d) if p["handle"] not in before]
        out: dict = {"new_paths": new}
        if not new:
            out["warning"] = ("No new Vehicle Tracking path was created. The pick may have landed on an existing path, "
                              "or the run was stopped; check the run log.")
        elif save:
            full = c3d._retry(lambda: c3d.doc().FullName) or ""
            if full:
                out["saved"] = c3d.qsave()
            else:
                out["saved"] = None
                out["note"] = "The drawing has never been saved, so it was not saved (that would open a Save As dialog)."
        return out

    ans = build_answers(direction, "Automatic", vehicle, vehicle_path, speed_policy, answers)
    # The pick point is queued and typed when Follow reaches its 'select object' prompt,
    # after any vehicle and drawing-settings dialogs. This is the order that worked live.
    run = c3d.runner.start("DRIVEFOLLOW\n", label=f"Follow {direction} at {pick_x:.1f},{pick_y:.1f}",
                           answers=ans, on_finish=after, inputs=[f"{pick_x:.3f},{pick_y:.3f}"])
    return c3d.runner.wait(run, budget_s)


def insert_profile(c3d: Civil3D, path_handle: str, x: float, y: float) -> dict:
    c3d.guard_write()
    res = c3d.lisp(f'(command "INSERTPROFILE" (handent "{path_handle}") "{x},{y}")')
    if res.get("ok") and res.get("result") == "nil":
        res["result"] = "inserted (the command returns nil)"
    return {"path": path_handle, "at": [x, y], **res}


def path_linework(c3d: Civil3D, path_handle: str) -> list[list[tuple[float, float]]]:
    """Explode a COPY of a VT path to read its envelope and wheel lines, then delete the copy."""
    c3d.guard_write()  # copies and explodes inside the drawing (net change is nil)
    # VT paths are custom entities; explode a copy with the EXPLODE command and
    # collect everything created after it.
    res = c3d.lisp(
        f'(progn (setq mcp:src (handent "{path_handle}")) (command "_.COPY" mcp:src "" "0,0" "0,0")'
        ' (setq mcp:cp (entlast) mcp:mark mcp:cp) (command "_.EXPLODE" mcp:cp "")'
        ' (setq mcp:h nil mcp:e (entnext mcp:mark))'
        ' (if (not (entget mcp:mark)) (setq mcp:e (entnext mcp:mark)))'
        ' (while mcp:e (setq mcp:h (cons (cdr (assoc 5 (entget mcp:e))) mcp:h) mcp:e (entnext mcp:e))) mcp:h)'
    )
    if not res.get("ok"):
        raise Civil3DError(f"Could not explode a copy of the path: {res.get('error') or res}")
    handles = [h.strip('"') for h in res["result"].strip("()").split() if h.strip('"')]
    lines: list[list[tuple[float, float]]] = []
    try:
        for h in handles:
            try:
                for pts, _closed, _k in entity_points(c3d, c3d.entity(h)):
                    if len(pts) >= 2:
                        lines.append(pts)
            except Exception:
                continue
    finally:
        for h in handles:
            try:
                c3d.entity(h).Delete()
            except Exception:
                pass
    if not lines:
        raise Civil3DError("The path did not explode into readable linework on this version.")
    return lines


def check_vt_path(c3d: Civil3D, path_handle: str, obstacles: list[Obstacle], required: float = 0.3) -> dict:
    """Clearance of a Vehicle Tracking path's drawn linework to obstacles.

    This measures what VT actually drew (envelope, body outlines, wheel tracks),
    so it catches cases where Follow dropped points and left the guide line.
    """
    lines = [LineString(p) for p in path_linework(c3d, path_handle)]
    rows = []
    for o in obstacles:
        d = min(o.geom.distance(l) for l in lines)
        crosses = any(o.geom.intersects(l) for l in lines)
        rows.append({"obstacle": o.label, "clearance_m": round(-0.0 if crosses else d, 3), "crosses": crosses})
    rows.sort(key=lambda r: (not r["crosses"], r["clearance_m"]))
    conflicts = [r for r in rows if r["crosses"]]
    tight = [r for r in rows if not r["crosses"] and r["clearance_m"] < required]
    return {
        "path": path_handle,
        "linework_pieces": len(lines),
        "pass": not conflicts and not tight,
        "conflicts": conflicts,
        "tight": tight,
        "nearest": rows[:5],
        "note": "Measured from VT's exploded linework; a line crossing an obstacle is reported as a conflict.",
    }


def annotate_png(png: str, metres_per_pixel: float, north_deg: float = 0.0, out: str | None = None, crop_dark: bool = True) -> dict:
    """Crop the dark CAD background off a PNGOUT, add a scale bar and north arrow."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(png).convert("RGB")
    if crop_dark:
        gray = im.convert("L").point(lambda v: 255 if v > 40 else 0)
        box = gray.getbbox()
        if box:
            im = im.crop(box)
    w, h = im.size
    pad = 70
    canvas = Image.new("RGB", (w, h + pad), "white")
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    # scale bar: pick a round length near 1/5 of the width
    target = w * metres_per_pixel / 5
    nice = min((x for x in (1, 2, 5, 10, 20, 25, 50, 100, 200) if x >= target), default=200)
    bar_px = int(nice / metres_per_pixel)
    x0, y0 = 20, h + 30
    for i in range(4):
        seg = bar_px // 4
        d.rectangle([x0 + i * seg, y0, x0 + (i + 1) * seg, y0 + 10], fill="black" if i % 2 == 0 else "white", outline="black")
    d.text((x0, y0 + 14), "0", fill="black", font=font)
    d.text((x0 + bar_px - 10, y0 + 14), f"{nice} m", fill="black", font=font)
    # north arrow
    import math

    cx, cy, r = w - 40, h + 35, 22
    a = math.radians(north_deg)
    tip = (cx + r * math.sin(a), cy - r * math.cos(a))
    left = (cx + 0.5 * r * math.sin(a - 2.6), cy - 0.5 * r * math.cos(a - 2.6))
    right = (cx + 0.5 * r * math.sin(a + 2.6), cy - 0.5 * r * math.cos(a + 2.6))
    d.polygon([tip, left, (cx, cy), right], fill="black")
    d.text((tip[0] - 5, tip[1] - 22), "N", fill="black", font=font)
    out = out or str(Path(png).with_name(Path(png).stem + "_fig.png"))
    canvas.save(out)
    return {"figure": out, "scale_bar_m": nice}
