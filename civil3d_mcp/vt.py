"""Autodesk Vehicle Tracking helpers.

Vehicle Tracking has no public API. These helpers drive its commands through
COM. Its dialogs are read and answered through the runner (runner.py): known,
confirmed dialogs are answered from the task's settings, everything else comes
back to Claude with its full contents. No screen access is needed.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from shapely.geometry import LineString

from .catalogue import HOME
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
    """Settings the VT workflow needs: no dynamic input, metres, single COPY, Standard text in Arial.

    The Standard style matters: with the old 'txt' font the inserted vehicle profile's values overprinted
    its labels (Hoima, 18 Oct 2026)."""
    c3d.set_var("DYNMODE", 0)
    c3d.set_var("INSUNITS", 6)
    c3d.set_var("COPYMODE", 1)
    font = None
    try:
        res = c3d.lisp('(progn (setq mcp:s (entget (tblobjname "STYLE" "Standard")))'
                       ' (if (wcmatch (strcase (cdr (assoc 3 mcp:s))) "TXT*,")'
                       ' (entmod (subst (cons 3 "arial.ttf") (assoc 3 mcp:s) mcp:s)))'
                       ' (cdr (assoc 3 (entget (tblobjname "STYLE" "Standard")))))')
        font = (res.get("result") or "").strip('"')
    except Exception:
        pass
    return {"DYNMODE": 0, "INSUNITS": 6, "COPYMODE": 1, "standard_font": font}


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
    c3d.activate()
    try:
        c3d.zoom_window(insert_x, insert_y, insert_x + w_px * metres_per_pixel, insert_y + h_px * metres_per_pixel)
    except Exception:
        pass
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
        # Yes only if the default vehicle named in the dialog is the task's vehicle; otherwise No, which
        # opens the Vehicle Library Explorer, where vehicle_path is selected.
        a["vt.default_vehicle"] = {"when_text_matches": re.escape(vehicle), "actions": [{"click": "Yes"}]}
        if vehicle_path:
            a["vt.default_vehicle"]["else_actions"] = [{"click": "No"}]
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


# ----------------------------------------------------------------------------- isolation around a run
def _layer_states(c3d: Civil3D) -> dict[str, dict]:
    return {l["name"]: l for l in c3d.layers()}


def _image_layers(c3d: Civil3D) -> set[str]:
    try:
        return {c3d._retry(lambda e=e: e.Layer) for e in c3d.select_on_layers("*", "IMAGE")}
    except Exception:
        return set()


def isolate_for_pick(c3d: Civil3D, guide_layer: str | None) -> dict:
    """Make the guide line the only thing a pick can land on.

    Live failures this prevents (Hoima, 18 Oct 2026): a pick on the guide where it crossed the attached A02
    image picked the image, and a pick where run 2's path overlapped the line picked the path; both times
    Follow ended silently with no path. Locks image and obstacle layers, turns off ATR* paths and the other
    guide layers. Returns what it changed so restore_layers can put it back."""
    states = _layer_states(c3d)
    lock = (_image_layers(c3d) | {n for n in states if n.upper().startswith(("SP-OBS", "LAYOUT-IMAGE"))})
    off = {n for n in states if (n.upper().startswith("ATR") or "GUIDE" in n.upper() or n.upper().startswith("LEC-"))}
    if guide_layer:
        off.discard(guide_layer)
        lock.discard(guide_layer)
    changed = {"locked": [], "turned_off": []}
    clayer = c3d.get_var("CLAYER")
    for n in sorted(lock):
        if not states.get(n, {}).get("locked"):
            changed["locked"].append(n)
    for n in sorted(off):
        if states.get(n, {}).get("on") and n != clayer:
            changed["turned_off"].append(n)
    if changed["locked"]:
        c3d.send('(command "_.-LAYER" "_LOCK" "' + ",".join(changed["locked"]) + '" "")')
    if changed["turned_off"]:
        c3d.send('(command "_.-LAYER" "_OFF" "' + ",".join(changed["turned_off"]) + '" "")')
    if guide_layer and (not states.get(guide_layer, {}).get("on", True)):
        c3d.send(f'(command "_.-LAYER" "_ON" "{guide_layer}" "")')
        changed["turned_on"] = [guide_layer]
    return changed


def restore_layers(c3d: Civil3D, changed: dict) -> None:
    if changed.get("turned_off"):
        c3d.send('(command "_.-LAYER" "_ON" "' + ",".join(changed["turned_off"]) + '" "")')
    if changed.get("locked"):
        c3d.send('(command "_.-LAYER" "_UNLOCK" "' + ",".join(changed["locked"]) + '" "")')


def check_pick(c3d: Civil3D, x: float, y: float, guide_handle: str | None, tol: float = 0.05) -> dict:
    """What a pick at (x, y) can hit: selectable entities within tol, after isolation."""
    data = c3d.lisp_data(
        f'(progn (setq mcp:ss (ssget "_C" (list {x - tol} {y - tol}) (list {x + tol} {y + tol}))) (setq mcp:l nil)'
        ' (if mcp:ss (repeat (setq mcp:i (sslength mcp:ss)) (setq mcp:i (1- mcp:i))'
        ' (setq mcp:e (entget (ssname mcp:ss mcp:i)) mcp:l (cons (list (cdr (assoc 5 mcp:e)) (cdr (assoc 0 mcp:e)) (cdr (assoc 8 mcp:e))) mcp:l))))'
        ' mcp:l)') or []
    states = _layer_states(c3d)
    # only what a pick can actually land on: visible, unlocked layers
    hits = [{"handle": h, "type": t, "layer": l} for h, t, l in data
            if states.get(l, {}).get("on", True) and not states.get(l, {}).get("frozen") and not states.get(l, {}).get("locked")]
    ok = bool(hits) and (guide_handle is None or any(hh["handle"].upper() == guide_handle.upper() for hh in hits))
    others = [hh for hh in hits if not guide_handle or hh["handle"].upper() != guide_handle.upper()]
    return {"ok": ok and not others, "hits": hits, "others": others}


def _pick_on_guide(c3d: Civil3D, guide_handle: str) -> tuple[float, float]:
    """A point on the guide line, away from its ends (middle of the longest straight)."""
    from .dwg import polyline_points

    pts = polyline_points(c3d, guide_handle)
    best, pos = -1.0, None
    for a, b in zip(pts, pts[1:]):
        d = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if d > best:
            best, pos = d, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return pos


def follow_run(
    c3d: Civil3D,
    pick_x: float | None,
    pick_y: float | None,
    direction: str = "Forwards",
    vehicle: str | None = None,
    vehicle_path: list[str] | None = None,
    speed_policy: str = "ask",
    preselect_path: str | None = None,
    answers: dict | None = None,
    save: bool = True,
    budget_s: float = 30.0,
    guide_handle: str | None = None,
    isolate: bool = True,
) -> dict:
    """Run DRIVEFOLLOW on a guide line with dialogs AND the pick handled by the server.

    v0.3: the pick point is typed into the command line through the window (WM_COPYDATA) when Follow
    reaches 'select object to Follow', because COM is refused at that prompt. Before the run the guide is
    isolated (image and obstacle layers locked, other paths and guides off) and the pick is checked to hit
    only the guide; after the run the layers are restored and a missing path is reported, not hidden.
    """
    c3d.guard_write()
    if c3d.runner.busy():
        raise Civil3DError(f"Run {c3d.runner.active.id} is still {c3d.runner.active.state}; finish it first.")
    try:
        names = c3d.get_var("CMDNAMES")
    except Civil3DError:
        names = "(busy)"
    if names:
        c3d.clear_command()  # PAN or a stale prompt blocked every call on 18 Oct 2026
    guide_layer = None
    if guide_handle:
        guide_layer = c3d._retry(lambda: c3d.entity(guide_handle).Layer)
        if pick_x is None or pick_y is None:
            pick_x, pick_y = _pick_on_guide(c3d, guide_handle)
    if pick_x is None or pick_y is None:
        raise Civil3DError("Give guide_handle (preferred) or pick_x/pick_y on the guide line.")
    before = {p["handle"] for p in list_paths(c3d)}
    changed = isolate_for_pick(c3d, guide_layer) if isolate else {}
    try:
        # Zoom in on the pick so the typed point lands on the guide line, not a neighbour.
        c3d.zoom_window(pick_x - 12, pick_y - 8, pick_x + 12, pick_y + 8)
        pick = check_pick(c3d, pick_x, pick_y, guide_handle)
        if not pick["ok"]:
            restore_layers(c3d, changed)
            return {"status": "not_started", "reason": "The pick point does not hit the guide line alone.", "pick_check": pick,
                    "next": "Give guide_handle, or a pick point on the guide away from other lines."}
    except Civil3DError:
        restore_layers(c3d, changed)
        raise
    if preselect_path:
        c3d.lisp(f'(sssetfirst nil (ssadd (handent "{preselect_path}")))')

    def after() -> dict:
        try:
            restore_layers(c3d, changed)
        except Exception:
            pass
        new = [p for p in list_paths(c3d) if p["handle"] not in before]
        out: dict = {"new_paths": new, "pick": [round(pick_x, 3), round(pick_y, 3)], "layers_restored": changed}
        if not new:
            out["warning"] = ("No new Vehicle Tracking path was created: the pick missed the guide, the run was "
                              "cancelled, or Follow could not drive the line. See the run log.")
        elif save:
            full = c3d._retry(lambda: c3d.doc().FullName) or ""
            if full:
                out["saved"] = c3d.qsave()
            else:
                out["saved"] = None
                out["note"] = "The drawing has never been saved, so it was not saved (that would open a Save As dialog)."
        return out

    ans = build_answers(direction, "Automatic", vehicle, vehicle_path, speed_policy, answers)
    run = c3d.runner.start("DRIVEFOLLOW\n", label=f"Follow {direction} at {pick_x:.1f},{pick_y:.1f}",
                           answers=ans, on_finish=after, inputs=[f"{pick_x:.3f},{pick_y:.3f}"],
                           prompt_patterns=[r"select object to follow", r"select object"])
    return c3d.runner.wait(run, budget_s)


_PROFILES = HOME / "profiles.json"


def _profiles() -> dict:
    try:
        return json.loads(_PROFILES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _image_extents(c3d: Civil3D) -> list[float] | None:
    boxes = []
    for e in c3d.select_on_layers("*", "IMAGE"):
        try:
            boxes.append(c3d.bbox(e))
        except Exception:
            pass
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


# ----------------------------------------------------------------------------- vehicle library
LIBRARY_CACHE = HOME / "vt_library.json"


def _flatten(nodes: list[dict], prefix: list[str] | None = None) -> list[list[str]]:
    out = []
    for n in nodes:
        path = (prefix or []) + [n["name"]]
        if n.get("children"):
            out.extend(_flatten(n["children"], path))
        else:
            out.append(path)
    return out


def library_cache() -> dict:
    try:
        return json.loads(LIBRARY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {"libraries": {}, "top": []}


def read_library(c3d: Civil3D, library: str | None = None) -> dict:
    """Open Vehicle Tracking's Vehicle Library Explorer, read its tree, close it, and cache the result.

    Opens the explorer the way Follow does (DRIVEFOLLOW, then No to the default-vehicle question), so it needs
    no screen and no click. library=None reads the libraries and their groups; library='South African Design
    Vehicles' (or a path like ['UK', 'FTA']) reads every vehicle under it. Nothing is drawn."""
    from . import dialogs as dlg

    c3d.guard_write()
    if c3d.runner.busy():
        raise Civil3DError(f"Run {c3d.runner.active.id} is still {c3d.runner.active.state}; finish it first.")
    try:
        if c3d.get_var("CMDNAMES"):
            c3d.clear_command()
    except Civil3DError:
        c3d.clear_command()
    run = c3d.runner.start("DRIVEFOLLOW\n", label="read vehicle library",
                           answers={"vt.default_vehicle": {"actions": [{"click": "No"}]}})
    res = c3d.runner.wait(run, 25)
    if res.get("status") != "dialog_pending":
        c3d.runner.cancel()
        return {"error": "The Vehicle Library Explorer did not open.", "run": res}
    snap = run.pending["snapshot"]
    if "library" not in " ".join(w.get("title", "") for w in snap["windows"]).lower():
        return res  # some other dialog: hand it to Claude as usual
    path = None if library is None else ([library] if isinstance(library, str) else list(library))
    try:
        dump = dlg.tree_dump(snap, path)
    finally:
        try:
            c3d.runner.act(run.pending["id"], [{"click": "Close"}], "library read; closing without choosing a vehicle", budget_s=5)
        except Exception:
            pass
        try:
            if c3d.runner.busy():
                c3d.runner.cancel()
        except Exception:
            pass
    cache = library_cache()
    if path is None:
        cache["top"] = dump["tree"]
    else:
        cache.setdefault("libraries", {})[" > ".join(path)] = dump["tree"]
    cache["read_at"] = time.strftime("%Y-%m-%d %H:%M")
    try:
        LIBRARY_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    vehicles = _flatten(dump["tree"]) if path else None
    out = {"window": dump.get("window"), "complete": dump["complete"], "tree": dump["tree"]}
    if vehicles is not None:
        out["vehicle_paths"] = vehicles
        out["count"] = len(vehicles)
        out["use"] = "Pass one of vehicle_paths to vt_follow_run(vehicle_path=...) with vehicle=<its last element>."
    return out


def find_vehicle(name: str) -> list[list[str]]:
    """Library paths in the cache whose vehicle name contains `name` (case-insensitive)."""
    cache = library_cache()
    out = []
    for key, tree in cache.get("libraries", {}).items():
        for p in _flatten(tree):
            if name.lower() in p[-1].lower():
                out.append(p)
    return out


def insert_profile(c3d: Civil3D, path_handle: str, x: float | None = None, y: float | None = None, force: bool = False) -> dict:
    """Insert a path's dimensioned vehicle profile once.

    A second INSERTPROFILE on the same path adds a second profile (it does not move the first), so the
    server records each insert and refuses a repeat unless force=True. With no x, y the profile goes in
    clear space to the right of the attached plan image, 22 m along for each profile already placed."""
    c3d.guard_write()
    full = c3d._retry(lambda: c3d.doc().FullName) or "(unsaved)"
    key = os.path.normcase(full)
    reg = _profiles()
    done = reg.get(key, {})
    if path_handle in done and not force:
        return {"error": f"Path {path_handle} already has a profile at {done[path_handle]}. A second insert duplicates it. "
                         "Move the existing one with its grip in Civil 3D, or pass force=True if a duplicate is really wanted."}
    auto = x is None or y is None
    if auto:
        ext = _image_extents(c3d)
        if ext is None:
            return {"error": "No attached image to place the profile beside; give x and y."}
        x = ext[2] + 4.0 + 22.0 * len(done)
        y = ext[3] - 3.0
    res = c3d.lisp(f'(command "INSERTPROFILE" (handent "{path_handle}") "{x},{y}")')
    if res.get("ok") and res.get("result") == "nil":
        res["result"] = "inserted (the command returns nil)"
    if res.get("ok"):
        done[path_handle] = [round(x, 3), round(y, 3)]
        reg[key] = done
        try:
            _PROFILES.write_text(json.dumps(reg, indent=1), encoding="utf-8")
        except Exception:
            pass
    return {"path": path_handle, "at": [round(x, 3), round(y, 3)], "placed": "auto, right of the plan" if auto else "given", **res}


def path_linework(c3d: Civil3D, path_handle: str) -> list[list[tuple[float, float]]]:
    """Explode a COPY of a VT path to read its envelope and wheel lines, then delete the copy."""
    c3d.guard_write()  # copies and explodes inside the drawing (net change is nil)
    # VT paths are custom entities; explode a copy with the EXPLODE command and
    # collect everything created after it.
    # v0.2 sent EXPLODE with a trailing "" and COPY in multiple mode; the extra Enter re-fired a command that
    # then waited for input and hung Civil 3D (twice on 18 Oct 2026). This is the form that worked live:
    # single COPY, EXPLODE with the entity only, everything after the mark collected.
    res = c3d.lisp(
        '(progn (setq mcp:cm (getvar "COPYMODE")) (setvar "COPYMODE" 1) (setq mcp:mark (entlast))'
        f' (command "_.COPY" (handent "{path_handle}") "" "0,0" "0,0")'
        ' (command "_.EXPLODE" (entlast))'
        ' (setvar "COPYMODE" mcp:cm)'
        ' (setq mcp:h nil mcp:e (entnext mcp:mark))'
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
