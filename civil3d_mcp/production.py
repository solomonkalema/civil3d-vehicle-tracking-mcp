"""Road design production steps (v0.9, 27 Oct 2026): the steps a road pilot had to do outside the server.

On a municipal roads pilot (27 Oct 2026) every production step - synchronise references, rebuild the
corridor, set title-block attributes, plot sheets, read cut/fill, write stake-out and setting-out data - was done
with throw-away scripts and long LISP, because the server could only read. Those scripts hit the same walls each
time: a plot run longer than one tool call, a PDF viewer locking the file just plotted, profile lines missing
until REGENALL, a plot style that wasn't installed, Total Volume Tables that can only be read by exploding them,
and corridor properties with the wrong name (OutOfDate, not IsOutOfDate). The tools here do those steps the
way that worked, inside the server's guards:

* Anything that changes a drawing calls guard_write (working copies and the private staging folder only).
* Anything that writes a file outside Civil 3D calls refuse_shared (never into a shared project folder).
* Every step keeps inside one tool call (about 35 s of work) and says what is left, rather than running on.

Reading quantities from the material sections Civil 3D keeps on each sample line gave the same cut/fill as the
Total Volume Table to within 0.3% on the pilot road, without adding or exploding a table in the drawing.
"""

from __future__ import annotations

import csv
import fnmatch
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from . import civil as civ
from . import roads
from .com_bridge import Civil3D, Civil3DError, refuse_shared

BUDGET_S = 35.0  # work per tool call; the bridge gives up at 60 s


def _items(c) -> list:
    return roads._items(c)


def _g(o, name, default=None):
    return roads._g(o, name, default)


# ----------------------------------------------------------------------------- references and corridors
def sync_references(c3d: Civil3D) -> dict:
    """Synchronise every data reference in the active (working) drawing, with the stale list before and after.

    A source renamed since the last sync raises 'Object Name Change' (Update the reference name / keep). That is
    the person's decision when the source belongs to someone else, so it comes back as a dialog, not answered.
    """
    c3d.guard_write()
    before = roads.drawing_report(c3d, ["alignments", "surfaces", "corridors"], False).get("needs_sync", [])
    res = c3d.send("_AeccSynchronizeReferences ", True, 40, label="sync references")
    if not res.get("idle", True):
        return {"before": before, "state": res,
                "next": "Still synchronising: c3d_run_status, then road_sync_references again to see what is left."}
    after = roads.drawing_report(c3d, ["alignments", "surfaces", "corridors"], False).get("needs_sync", [])
    out = {"before": before, "after": after, "synchronised": len(before) - len(after)}
    if after:
        out["note"] = ("Still flagged after a sync. Edge alignments and EG profiles can stay flagged with geometry "
                       "identical to the source (seen on the pilot road); check one against the source before acting.")
    return out


def rebuild_corridors(c3d: Civil3D, names: list[str] | None = None, only_out_of_date: bool = False) -> dict:
    """Rebuild corridors in the active (working) drawing, one at a time, within one call's time budget."""
    c3d.guard_write()
    rd = roads.road_doc(c3d)
    t0 = time.time()
    done, left = [], []
    for c in _items(c3d._retry(lambda: rd.Corridors)):
        name = c.Name
        if names and name not in names:
            continue
        was = bool(_g(c, "OutOfDate", False))
        if only_out_of_date and not was:
            continue
        if done and time.time() - t0 > BUDGET_S * 0.5:
            left.append(name)  # a rebuild can take tens of seconds: don't start one we can't finish
            continue
        s = time.time()
        c3d._retry(lambda: c.Rebuild(), timeout=120)
        done.append({"corridor": name, "was_out_of_date": was, "now_out_of_date": bool(_g(c, "OutOfDate", False)),
                     "seconds": round(time.time() - s, 1)})
    out = {"rebuilt": done}
    if left:
        out["left"] = left
        out["next"] = "Call road_rebuild_corridors(names=left) for the rest."
    if not done and not left:
        out["note"] = "No corridor matched." if names else "No corridor in this drawing."
    return out


# ----------------------------------------------------------------------------- title blocks
def set_titleblock(c3d: Civil3D, values: dict[str, str], layouts: list[str] | None = None,
                   block: str | None = None) -> dict:
    """Set title-block attributes by tag on every (or the named) layout. Only the tags given change.

    block: block name or wildcard (case-insensitive); default: any block on the sheet carrying one of the tags.
    """
    if not values:
        raise Civil3DError("Give the attributes to set, e.g. {'DESIGNED': 'AB', 'CHECKED': 'CD'}.")
    c3d.guard_write()
    want = {k.upper(): str(v) for k, v in values.items()}
    doc = c3d.doc()
    changes, untouched = [], []
    for L in _items(c3d._retry(lambda: doc.Layouts)):
        if L.Name == "Model" or (layouts and L.Name not in layouts):
            continue
        hit = False
        for e in _items(_g(L, "Block")):
            if _g(e, "ObjectName") != "AcDbBlockReference" or not _g(e, "HasAttributes", False):
                continue
            bname = _g(e, "EffectiveName") or _g(e, "Name") or ""
            if block and not fnmatch.fnmatch(bname.lower(), block.lower()):
                continue
            for a in e.GetAttributes():
                tag = a.TagString.upper()
                if tag in want:
                    hit = True
                    old = a.TextString
                    if old != want[tag]:
                        a.TextString = want[tag]
                        changes.append({"layout": L.Name, "block": bname, "tag": tag, "old": old, "new": want[tag]})
        if not hit:
            untouched.append(L.Name)
    out = {"changed": changes, "count": len(changes)}
    if untouched:
        out["layouts_without_these_tags"] = untouched
    return out


# ----------------------------------------------------------------------------- plotting
def _locked(p: Path) -> bool:
    """True if another program holds the file (a PDF viewer opened after the last plot, typically)."""
    try:
        with open(p, "ab"):
            return False
    except OSError:
        return True


def free_name(p: Path) -> Path:
    """p itself if unused, else 'name v2.pdf', 'name v3.pdf' ... (the first that does not exist)."""
    if not p.exists():
        return p
    n = 2
    while True:
        q = p.with_name(f"{p.stem} v{n}{p.suffix}")
        if not q.exists():
            return q
        n += 1


def plot_target(folder: Path, name: str, overwrite: bool) -> tuple[Path, str | None]:
    """Where a sheet goes, and a note if it could not go where asked."""
    p = folder / (name if name.lower().endswith(".pdf") else f"{name}.pdf")
    if not p.exists():
        return p, None
    if overwrite and not _locked(p):
        p.unlink()
        return p, None
    q = free_name(p)
    why = "is open in another program (a PDF viewer)" if _locked(p) else "already exists (overwrite=False)"
    return q, f"'{p.name}' {why}; plotted as '{q.name}'"


def _lisp_str(s: str) -> str:
    return '"' + s.replace("\\", "/").replace('"', '\\"') + '"'


def plot_check(c3d: Civil3D, layouts: list[str] | None = None) -> dict:
    """What a plot of these layouts would use, and anything that would spoil it (a missing plot style)."""
    doc = c3d.doc()
    try:
        ctb_dir = Path(c3d._retry(lambda: c3d.app().Preferences.Files.PrinterStyleSheetPath).split(";")[0])
    except Exception:
        ctb_dir = None
    rows = []
    for L in _items(c3d._retry(lambda: doc.Layouts)):
        if L.Name == "Model" or (layouts and L.Name not in layouts):
            continue
        ctb = _g(L, "StyleSheet") or ""
        r = {"layout": L.Name, "tab": _g(L, "TabOrder"), "device": _g(L, "ConfigName"), "ctb": ctb,
             "paper": _g(L, "CanonicalMediaName")}
        if ctb and ctb_dir is not None:
            r["ctb_installed"] = (ctb_dir / ctb).exists()
        rows.append(r)
    rows.sort(key=lambda r: r.get("tab") or 0)
    return {"plot_style_folder": str(ctb_dir) if ctb_dir else None, "layouts": rows}


def install_ctb(c3d: Civil3D, source: str) -> dict:
    """Copy a plot style (.ctb/.stb) into Civil 3D's plot-style folder, so sheets plot with it."""
    import shutil

    src = Path(source)
    if src.suffix.lower() not in (".ctb", ".stb") or not src.exists():
        raise Civil3DError(f"Not a plot style file: {source}")
    dst_dir = Path(c3d._retry(lambda: c3d.app().Preferences.Files.PrinterStyleSheetPath).split(";")[0])
    dst = dst_dir / src.name
    if dst.exists():
        return {"installed": str(dst), "note": "already there; left as it is"}
    shutil.copy2(src, dst)
    return {"installed": str(dst)}


def plot_layouts(c3d: Civil3D, folder: str, layouts: list[str] | None = None, names: list[str] | None = None,
                 regen: bool = True, overwrite: bool = False, allow_missing_ctb: bool = False) -> dict:
    """Plot layouts of the active drawing to PDF with each layout's own page set-up, one sheet per -PLOT.

    What the road pilot learned, built in:
    * BACKGROUNDPLOT 0 while plotting (else -PLOT returns before the file exists), restored afterwards.
    * REGENALL first: without it the profile lines were missing from the PP sheets.
    * The DWG To PDF driver opens the PDF in the viewer afterwards, which locks it; a second plot to the same name
      then stalls at the file-name prompt. A file that exists or is locked gets a new name ('... v2.pdf').
    * A missing plot style plots everything in its own colours without complaint: checked first.
    * As many sheets as fit in one call; the rest come back in 'left'.
    """
    out_dir = Path(folder)
    refuse_shared(out_dir, "plot into")
    out_dir.mkdir(parents=True, exist_ok=True)
    chk = plot_check(c3d, layouts)
    rows = chk["layouts"]
    if layouts:
        missing = [n for n in layouts if n not in {r["layout"] for r in rows}]
        if missing:
            raise Civil3DError(f"No layout named {missing}. road_sheets lists them.")
        rows = sorted(rows, key=lambda r: layouts.index(r["layout"]))
    if names and len(names) != len(rows):
        raise Civil3DError(f"names has {len(names)} entries for {len(rows)} layouts.")
    no_ctb = sorted({r["ctb"] for r in rows if r.get("ctb_installed") is False})
    if no_ctb and not allow_missing_ctb:
        return {"plotted": [], "missing_plot_styles": no_ctb, "plot_style_folder": chk["plot_style_folder"],
                "next": "Install the plot style first (road_plot_sheets(install_ctb=<path to the .ctb>)), or pass "
                        "allow_missing_ctb=True to plot without it."}
    doc = c3d.doc()
    drawing = c3d._retry(lambda: doc.FullName)
    tab0 = c3d._retry(lambda: doc.GetVariable("CTAB"))
    bp0 = c3d._retry(lambda: doc.GetVariable("BACKGROUNDPLOT"))
    t0 = time.time()
    plotted, left, notes = [], [], []
    stop = None
    try:
        doc.SetVariable("BACKGROUNDPLOT", 0)
        if regen:
            # REGENALL on a PnP with data references and xrefs took 90 s on 27 Oct: give it a call of its own.
            res = c3d.send("_.REGENALL ", True, 40, label="regenall")
            if not res.get("idle", True) or time.time() - t0 > 15:
                return {"regenerated": res.get("idle", True), "plotted": [], "left": [r["layout"] for r in rows],
                        "seconds": round(time.time() - t0, 1),
                        "next": "REGENALL took most of this call. Once c3d_status shows cmdactive 0, call "
                                "road_plot_sheets again with regen=False."}
        for i, r in enumerate(rows):
            if stop or (plotted and time.time() - t0 > BUDGET_S):
                left.append(r["layout"])
                continue
            now = c3d._retry(lambda: c3d.doc().FullName)
            if now != drawing:
                # Another client (a second Claude session on the same Civil 3D, 27 Oct) switched the active drawing:
                # the next -PLOT would go to the wrong drawing.
                stop = f"The active drawing changed to '{now}' while plotting '{drawing}'. Stopped."
                left.append(r["layout"])
                continue
            target, note = plot_target(out_dir, (names[i] if names else r["layout"]).replace("/", "-"), overwrite)
            if note:
                notes.append(note)
            s = time.time()
            res = c3d.lisp(f'(progn (setvar "CTAB" {_lisp_str(r["layout"])}) '
                           f'(command "_.-PLOT" "_N" {_lisp_str(r["layout"])} "" "" {_lisp_str(str(target))} "_N" "_Y") '
                           f'(findfile {_lisp_str(str(target))}))', timeout=40)
            end = time.time() + 10
            while not (target.exists() and target.stat().st_size > 0) and time.time() < end:
                time.sleep(0.3)
            ok = target.exists() and target.stat().st_size > 0 and target.stat().st_mtime >= s - 2
            row = {"layout": r["layout"], "pdf": str(target), "ok": ok, "seconds": round(time.time() - s, 1)}
            if ok:
                row["kb"] = round(target.stat().st_size / 1024)
            else:
                row["lisp"] = res
            plotted.append(row)
    finally:
        for var, val in (("BACKGROUNDPLOT", int(bp0)), ("CTAB", tab0)):
            try:
                doc.SetVariable(var, val)  # on the drawing plotted, even if another one is active now
            except Exception:
                pass
    out = {"plotted": plotted, "folder": str(out_dir), "drawing": drawing}
    if stop:
        out["stopped"] = stop
        out["next_if_stopped"] = ("Only one client should drive Civil 3D at a time. Ask the person which session is "
                                  "using it, then activate the drawing again and plot 'left'.")
    if notes:
        out["renamed"] = notes
    if no_ctb:
        out["missing_plot_styles"] = no_ctb
    if left:
        out["left"] = left
        out["next"] = "Call road_plot_sheets again with layouts=left (and the matching names) for the rest."
    out["check"] = "Look at each PDF with c3d_pdf_page before calling a sheet done: profile lines, labels, title block."
    return out


# ----------------------------------------------------------------------------- quantities
def _links(sec) -> list:
    L = sec.Links
    return [((L.Item(i).StartPointX, L.Item(i).StartPointY), (L.Item(i).EndPointX, L.Item(i).EndPointY))
            for i in range(L.Count)]


def loops(segs: list, tol: float = 1e-4) -> list[list[tuple[float, float]]]:
    """Chain link segments (pairs of points) into closed rings."""
    def key(p):
        return round(p[0] / tol), round(p[1] / tol)

    starts = defaultdict(list)
    for i, (a, _) in enumerate(segs):
        starts[key(a)].append(i)
    used, out = set(), []
    for i in range(len(segs)):
        if i in used:
            continue
        ring, j = [], i
        while j is not None and j not in used:
            used.add(j)
            a, b = segs[j]
            ring.append(a)
            nxt = [k for k in starts.get(key(b), []) if k not in used]
            j = nxt[0] if nxt else None
        if len(ring) >= 3:
            out.append(ring)
    return out


def ring_area(ring) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))) / 2


def _centroid(ring) -> tuple[float, float]:
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def _elev_at(segs, x: float):
    for (x0, y0), (x1, y1) in segs:
        if min(x0, x1) <= x <= max(x0, x1) and x1 != x0:
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return None


def average_end_area(rows: list[dict]) -> dict[str, float]:
    """Volumes between consecutive stations by the average end area method (as Civil 3D's volume report)."""
    rows = sorted(rows, key=lambda r: r["station"])
    keys = sorted({k for r in rows for k in r if k != "station"})
    vols = {k: 0.0 for k in keys}
    for a, b in zip(rows, rows[1:]):
        d = b["station"] - a["station"]
        for k in keys:
            vols[k] += d * (a.get(k, 0.0) + b.get(k, 0.0)) / 2
    return {k: round(v, 2) for k, v in vols.items()}


GROUND_WORDS = ("topo", "existing", "eg", "ground", "survey", "ogl")
DESIGN_WORDS = ("top", "datum", "corridor", "design", "finished", "fg")


def pick_ground(names: list[str], ground: str | None = None) -> int | None:
    """Index of the existing-ground section among surface section names.

    Given `ground`, the first name containing it. Otherwise a name with a ground word ('Road A Topo'), else the
    first that names no corridor or design surface. (On the pilot the CS drawing also samples 'Road A Corridor'
    itself, which the first v0.9 draft took for the ground.)
    """
    low = [n.lower() for n in names]
    if ground:
        return next((i for i, n in enumerate(low) if ground.lower() in n), None)
    words = [set(n.replace("-", " ").replace("_", " ").split()) for n in low]
    for i, w in enumerate(words):
        if w & set(GROUND_WORDS) and not w & set(DESIGN_WORDS):
            return i
    return next((i for i, w in enumerate(words) if not w & set(DESIGN_WORDS)), None)


def _section_name(s) -> str:
    """'Road A SLG - Earthworks (1)' -> 'Earthworks'."""
    return s.Name.split(" - ")[-1].rsplit("(", 1)[0].strip()


_qty_progress: dict[tuple, dict] = {}  # (drawing, alignment, group, earthworks, ground) -> rows read so far


def quantities(c3d: Civil3D, alignment: str, group: str | None = None, earthworks: str = "Earthworks",
               ground: str | None = None, out_csv: str | None = None, detail: bool = False) -> dict:
    """Cut/fill and every material's volume for one sample line group, from its material sections.

    Earthworks rings are classed cut or fill by whether they sit below or above the existing-ground section
    (`ground`: part of its name; default from pick_ground). Run Compute Materials in Civil 3D first; this only reads.
    Reading links over COM is slow (32 sections took over a minute on the pilot road), so a call stops at the time budget
    and keeps what it read: call again with the same arguments to carry on.
    """
    al = civ.get_alignment(c3d, alignment)
    groups = _items(al.SampleLineGroups)
    if not groups:
        raise Civil3DError(f"'{alignment}' has no sample line group.")
    g = next((x for x in groups if x.Name == group), None) if group else groups[0]
    if g is None:
        raise Civil3DError(f"No sample line group '{group}'; there are {[x.Name for x in groups]}.")
    drawing = c3d.doc().FullName
    key = (drawing, alignment, g.Name, earthworks, ground)
    st = _qty_progress.setdefault(key, {"rows": {}, "ground": None, "materials": set(), "seen": set()})
    lines = _items(g.SampleLines)
    if not lines:
        raise Civil3DError(f"Sample line group '{g.Name}' has no sample lines.")
    t0 = time.time()
    for idx, sl in enumerate(lines):
        if idx in st["rows"]:
            continue
        if time.time() - t0 > BUDGET_S:
            break
        secs = _items(sl.Sections)
        surf = [x for x in secs if _g(x, "DataType") != 5]
        mats = [x for x in secs if _g(x, "DataType") == 5]
        names = [_section_name(x) for x in surf]
        st["seen"].update(names)
        i = pick_ground(names, ground)
        eg_segs = _links(surf[i]) if i is not None else []
        if i is not None:
            st["ground"] = names[i]
        row = {"station": round(float(sl.Station), 3), "cut": 0.0, "fill": 0.0}
        for x in mats:
            n = _section_name(x)
            rings = loops(_links(x))
            if n == earthworks:
                for r in rings:
                    cx, cy = _centroid(r)
                    z = _elev_at(eg_segs, cx)
                    row["cut" if z is not None and cy < z else "fill"] += ring_area(r)
            else:
                st["materials"].add(n)
                row[n] = row.get(n, 0.0) + sum(ring_area(r) for r in rings)
        st["rows"][idx] = row
    if len(st["rows"]) < len(lines):
        return {"alignment": alignment, "sample_line_group": g.Name, "partial": True,
                "sections_read": len(st["rows"]), "sections": len(lines),
                "next": "Call road_quantities again with the same arguments to carry on."}
    _qty_progress.pop(key, None)
    rows = sorted(st["rows"].values(), key=lambda r: r["station"])
    materials = st["materials"]
    res = {"alignment": alignment, "sample_line_group": g.Name, "drawing": drawing,
           "from": rows[0]["station"], "to": rows[-1]["station"], "sections": len(rows),
           "ground_section": st["ground"], "surface_sections": sorted(st["seen"]), "materials": sorted(materials),
           "volumes_m3": average_end_area(rows)}
    if not st["ground"]:
        res["warning"] = "No existing-ground section found: every earthworks ring was counted as fill. Pass ground=."
    if not materials and not any(r["cut"] or r["fill"] for r in rows):
        res["warning"] = "No material sections. Run Compute Materials (_AeccComputeMaterials) on a working copy first."
    if out_csv:
        p = Path(out_csv)
        refuse_shared(p, "write")
        p.parent.mkdir(parents=True, exist_ok=True)
        keys = ["station", "cut", "fill"] + sorted(materials)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([k if k == "station" else f"{k} area m2" for k in keys])
            for r in rows:
                w.writerow([round(r.get(k, 0.0), 3) for k in keys])
        res["csv"] = str(p)
    if detail:
        res["stations"] = [{k: round(v, 3) for k, v in r.items()} for r in rows]
    return res


# ----------------------------------------------------------------------------- stake-out and setting-out
def _point(al, s: float, o: float) -> tuple[float, float]:
    import pythoncom
    import win32com.client

    try:
        r = al.PointLocation(s, o)
        return float(r[0]), float(r[1])
    except TypeError:
        e = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, 0.0)
        n = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, 0.0)
        al.PointLocation(s, o, e, n)
        return float(e.value), float(n.value)


def _offset_to(cl, target, s: float, guess: float):
    """Offset from the centre line at station s to where its perpendicular meets the target alignment."""
    d = guess
    for _ in range(8):
        x, y = _point(cl, s, d)
        try:
            off = float(target.StationOffset(x, y)[1])
        except Exception:
            return None
        d -= off
        if abs(off) < 0.001:
            return round(d, 3)
    return round(d, 3) if abs(off) < 0.01 else None


def stations_every(start: float, end: float, step: float) -> list[float]:
    """start, start+step, ... and always the exact end station (a rounded end gives 'Invalid input')."""
    out, s = [], start
    while s < end - 1e-6:
        out.append(round(s, 3))
        s += step
    out.append(end)
    return out


def stakeout(c3d: Civil3D, alignment: str, interval: float = 10.0, surface: str | None = None,
             targets: dict[str, str] | None = None, out_csv: str | None = None) -> dict:
    """Stake-out table: centre line and target edges at a fixed interval, with levels from a surface.

    targets: {label: alignment name}; default every alignment named '<alignment>-...' (edges, walkways, drains).
    surface: default '<alignment> Corridor - Top' if the drawing has it.
    """
    ldoc = civ.aecc_doc(c3d)
    al_by = {a.Name: a for a in civ._alignments(c3d)}
    cl = al_by.get(alignment)
    if cl is None:
        raise Civil3DError(f"No alignment named '{alignment}'.")
    surfs = {s.Name: s for s in _items(ldoc.Surfaces)}
    sname = surface or f"{alignment} Corridor - Top"
    top = surfs.get(sname)
    if targets is None:
        targets = {n[len(alignment) + 1:].strip(): n for n in al_by if n.startswith(f"{alignment}-")}
    missing = [n for n in targets.values() if n not in al_by]
    rows = []
    for s in stations_every(float(cl.StartingStation), float(cl.EndingStation), float(interval)):
        r = {"station": round(s, 3)}
        x, y = _point(cl, s, 0.0)
        r["CL"] = {"e": round(x, 3), "n": round(y, 3), "off": 0.0}
        for label, tn in targets.items():
            t = al_by.get(tn)
            if t is None:
                continue
            guess = -4.0 if "left" in label.lower() else 4.0
            o = _offset_to(cl, t, s, guess)
            if o is not None:
                xx, yy = _point(cl, s, o)
                r[label] = {"e": round(xx, 3), "n": round(yy, 3), "off": o}
        if top is not None:
            for v in r.values():
                if isinstance(v, dict):
                    try:
                        v["z"] = round(float(top.FindElevationAtXY(v["e"], v["n"])), 3)
                    except Exception:
                        v["z"] = None
        rows.append(r)
    res = {"alignment": alignment, "interval": interval, "stations": len(rows), "targets": targets,
           "surface": sname if top is not None else None, "first": rows[0], "last": rows[-1]}
    if top is None:
        res["warning"] = f"No surface '{sname}' in this drawing: no levels. Pass surface= (e.g. the corridor Top)."
    else:
        no_z = [r["station"] for r in rows if r["CL"].get("z") is None]
        if no_z:
            res["stations_without_level"] = no_z  # outside the surface, e.g. before the corridor starts

    if missing:
        res["targets_missing"] = missing
    if out_csv:
        p = Path(out_csv)
        refuse_shared(p, "write")
        p.parent.mkdir(parents=True, exist_ok=True)
        cols = ["CL"] + list(targets)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Station"] + [f"{c} {k}" for c in cols for k in ("offset", "easting", "northing", "level")])
            for r in rows:
                line = [r["station"]]
                for c in cols:
                    v = r.get(c) or {}
                    line += [v.get("off"), v.get("e"), v.get("n"), v.get("z")]
                w.writerow(line)
        res["csv"] = str(p)
    return res


_H = ["Type", "StartingStation", "EndingStation", "Length", "Radius", "Clockwise", "Delta", "StartEasting",
      "StartNorthing", "EndEasting", "EndNorthing", "PIEasting", "PINorthing", "CenterEasting", "CenterNorthing",
      "Direction", "Tangent", "External", "MidOrdinate", "ChordLength"]
_V = ["Station", "Elevation", "GradeIn", "GradeOut", "CurveLength"]
_HTYPES = {1: "tangent", 2: "arc", 3: "spiral", 4: "spiral-curve-spiral", 5: "spiral-tangent-spiral",
           6: "spiral-tangent", 7: "tangent-spiral", 8: "spiral-curve", 9: "curve-spiral"}


def _props(o, names) -> dict:
    out = {}
    for n in names:
        try:
            v = getattr(o, n)
        except Exception:
            continue
        out[n] = round(v, 4) if isinstance(v, float) else v if isinstance(v, (int, str, bool)) or v is None else str(v)
    return out


def setout_data(c3d: Civil3D, alignment: str, profiles: list[str] | None = None) -> dict:
    """Horizontal curve data (every alignment entity) and the vertical PVIs of its layout profiles."""
    al = civ.get_alignment(c3d, alignment)
    horiz = sorted((_props(e, _H) for e in _items(al.Entities)), key=lambda r: r.get("StartingStation", 0))
    for r in horiz:  # the Entities collection is not in station order; types and angles come as codes and radians
        r["Type"] = _HTYPES.get(r.get("Type"), r.get("Type"))
        for k in ("Delta", "Direction"):
            if isinstance(r.get(k), float):
                r[f"{k}_deg"] = round(math.degrees(r.pop(k)), 4)
    res = {"alignment": alignment, "start": round(float(al.StartingStation), 3), "end": round(float(al.EndingStation), 3),
           "horizontal": horiz, "profiles": {}}
    for p in _items(al.Profiles):
        if profiles and p.Name not in profiles:
            continue
        if not profiles and _g(p, "Type") != 2:  # layout (design) profiles only by default
            continue
        pvis = [_props(v, _V) for v in _items(_g(p, "PVIs"))]
        for v in pvis:
            g1, g2, L = v.get("GradeIn"), v.get("GradeOut"), v.get("CurveLength")
            if isinstance(g1, (int, float)) and isinstance(g2, (int, float)) and L and g1 != g2:
                v["K"] = round(L / abs((g2 - g1) * 100), 2)  # grades are fractions
        res["profiles"][p.Name] = pvis
    return res


# ----------------------------------------------------------------------------- session
def quit_civil3d(c3d: Civil3D, confirm: bool = False) -> dict:
    """Quit Civil 3D cleanly when nothing open has unsaved changes.

    Closing data-shortcut drawings one by one over COM crashed Civil 3D four times on 26 Oct 2026; quitting the
    whole program with only read-only or saved drawings open did not. Use this to restart between batches.
    """
    app = c3d.app()
    docs = []
    for d in _items(c3d._retry(lambda: app.Documents)):
        docs.append({"drawing": _g(d, "FullName") or _g(d, "Name"), "read_only": bool(_g(d, "ReadOnly", False)),
                     "saved": bool(_g(d, "Saved", True))})
    unsaved = [d for d in docs if not d["saved"]]
    if unsaved:
        return {"refused": "These drawings have unsaved changes; quitting would ask about each one.",
                "unsaved": unsaved, "next": "Save working copies with c3d_save. A read-only drawing with changes "
                "(e.g. a changed layout tab) is for the person to discard in Civil 3D."}
    if not confirm:
        return {"would_quit": docs, "next": "Call again with confirm=True to quit Civil 3D."}
    c3d._retry(lambda: app.Quit(), timeout=20)
    c3d._app = None
    civ._cache.clear()
    roads._road.clear()
    return {"quit": True, "drawings_were_open": docs,
            "next": "Start again with c3d_launch(folder, name) when ready."}


def write_json(path: str, data) -> str:
    p = Path(path)
    refuse_shared(p, "write")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return str(p)


def focus_window() -> dict:
    """Bring the running Civil 3D window to the front (for computer use), without starting another Civil 3D."""
    from . import dialogs as dlg
    from . import watchdog

    pids = watchdog.civil_pids()
    h = watchdog.main_window(pids)
    if not h:
        return {"error": "No Civil 3D window is open. Never start Civil 3D from computer use; use c3d_launch."}
    import win32con

    if dlg.win32gui.IsIconic(h):
        dlg.win32gui.ShowWindow(h, win32con.SW_RESTORE)
    try:
        dlg.win32gui.SetForegroundWindow(h)
    except Exception:
        # Windows refuses SetForegroundWindow from a background process unless input was sent: nudge with Alt.
        import win32api

        win32api.keybd_event(0x12, 0, 0, 0)
        dlg.win32gui.SetForegroundWindow(h)
        win32api.keybd_event(0x12, 0, 2, 0)
    return {"focused": dlg.win32gui.GetWindowText(h), "hwnd": h, "pids": sorted(p for p, n in pids.items() if n == "acad.exe")}

