"""Road production steps that used to need the screen or long LISP (added 11 Nov 2026).

Each one was first proven by hand on a seven-road production run (knowledge.md section 14):
- set_profile_pvis: replace the interior PVIs of a design profile (remove all, add new, verify).
- profile_levels: station / level table of an alignment's profiles, written to a text file.
- rebuild_corridor_surfaces: Rebuild() on the corridor surfaces (rebuild_corridors does not do it).
- replace_text: replace a string in every text, mtext and attribute of the drawing (blocks included).
- plot_model_window: plot a model-space window to PDF at a fixed scale (section pages without layouts).
- section_label_weeding / label_style_text: written against the COM API as found on 11 Nov; they report clearly
  when the property is not reachable so the caller can fall back to the screen.

All writes go through guard_write (working copies only) and refuse_shared (never into a shared project folder).
"""
from __future__ import annotations

import time
from pathlib import Path

from . import civil as civ
from .com_bridge import Civil3D, Civil3DError, refuse_shared


def _items(c) -> list:
    try:
        return [c.Item(i) for i in range(c.Count)]
    except Exception:
        return []


def _g(o, name, default=None):
    try:
        return getattr(o, name)
    except Exception:
        return default


def _profile(c3d: Civil3D, alignment: str, profile: str | None):
    al = civ.get_alignment(c3d, alignment)
    profs = _items(al.Profiles)
    if profile:
        for p in profs:
            if p.Name == profile:
                return al, p
        raise Civil3DError(f"No profile named '{profile}' on '{alignment}'. It has: {[p.Name for p in profs]}")
    design = [p for p in profs if _g(p, "Type") == 2]
    if len(design) != 1:
        raise Civil3DError(f"Name the profile: '{alignment}' has {[p.Name for p in profs]}")
    return al, design[0]


def _pvi_rows(p) -> list[dict]:
    rows = []
    for v in _items(p.PVIs):
        r = {"station": round(float(v.Station), 4), "level": round(float(v.Elevation), 4)}
        L = _g(v, "CurveLength")
        if isinstance(L, (int, float)):
            r["length"] = round(float(L), 3)
        rows.append(r)
    return sorted(rows, key=lambda r: r["station"])


# ----------------------------------------------------------------------------- profile editing
def set_profile_pvis(c3d: Civil3D, alignment: str, pvis: list[dict], profile: str | None = None) -> dict:
    """Replace the INTERIOR PVIs of a design profile. The first and last points (tie-ins) are kept.

    pvis: [{"station": s, "level": z, "length": L}], L = 0 or missing for a grade break without a curve.
    Returns the PVIs before and after and any curve Civil 3D silently turned into a tangent PVI (it does that when
    a curve touches its neighbour or the profile start: shorten it and call again).
    """
    if not pvis:
        raise Civil3DError("Give the new interior PVIs: [{'station': .., 'level': .., 'length': ..}, ...]")
    c3d.guard_write()
    al, p = _profile(c3d, alignment, profile)
    before = _pvi_rows(p)
    if len(before) < 2:
        raise Civil3DError("The profile has fewer than two points.")
    s0, s1 = before[0]["station"], before[-1]["station"]
    new = sorted(({"station": float(v["station"]), "level": float(v["level"]), "length": float(v.get("length") or 0)}
                  for v in pvis), key=lambda v: v["station"])
    for i, v in enumerate(new):
        if not (s0 < v["station"] < s1):
            raise Civil3DError(f"PVI at {v['station']} is outside the profile ({s0} to {s1}).")
        a = v["station"] - v["length"] / 2
        b = v["station"] + v["length"] / 2
        lo = s0 if i == 0 else new[i - 1]["station"] + new[i - 1]["length"] / 2
        if a < lo - 1e-6 or b > s1 + 1e-6:
            raise Civil3DError(f"The curve at {v['station']} (L {v['length']}) overlaps its neighbour or the profile end.")
    # remove the interior PVIs; the collection must be fetched again after every change
    guard = 0
    while guard < 200:
        coll = p.PVIs
        if coll.Count <= 2:
            break
        c3d._retry(lambda: coll.Remove(1))
        guard += 1
    errors = []
    for v in new:
        coll = p.PVIs
        try:
            if v["length"] > 0:
                c3d._retry(lambda: coll.Add(v["station"], v["level"], 3, v["length"]))  # 3 = symmetric parabola
            else:
                c3d._retry(lambda: coll.Add(v["station"], v["level"], 1))  # 1 = tangent PVI
        except Exception as e:  # noqa: BLE001  Add can raise yet still add a tangent PVI
            errors.append({"station": v["station"], "error": str(e)[:120]})
    after = _pvi_rows(p)
    lost = []
    for v in new:
        m = [r for r in after if abs(r["station"] - v["station"]) < 0.01]
        if not m:
            lost.append({"station": v["station"], "problem": "missing"})
        elif v["length"] > 0 and not m[0].get("length"):
            lost.append({"station": v["station"], "problem": "added as a tangent PVI (no curve): shorten the curve"})
    out = {"alignment": alignment, "profile": p.Name, "before": before, "after": after}
    if errors:
        out["add_errors"] = errors
    if lost:
        out["fix"] = lost
        out["next"] = "Call again with the listed curves 0.1 to 1 m shorter. Nothing is saved yet."
    else:
        out["next"] = "c3d_save, then corridor drawing: road_sync_references, road_rebuild_corridors, road_rebuild_corridor_surfaces."
    return out


def profile_levels(c3d: Civil3D, alignment: str, interval: float = 20.0, profiles: list[str] | None = None,
                   out_txt: str | None = None) -> dict:
    """Levels of an alignment's profiles at a regular interval (plus the end station).

    out_txt writes 'station level1 level2 ...' (one column per profile, in the order returned)."""
    al = civ.get_alignment(c3d, alignment)
    profs = [p for p in _items(al.Profiles) if not profiles or p.Name in profiles]
    if not profs:
        raise Civil3DError(f"No matching profile on '{alignment}'.")
    s0, s1 = float(al.StartingStation), float(al.EndingStation)
    stas = []
    s = s0
    while s < s1 - 1e-6:
        stas.append(round(s, 4))
        s += interval
    stas.append(round(s1, 4))
    rows = []
    for s in stas:
        row = [s]
        for p in profs:
            z = None
            for ss in (s, s - 0.001, s + 0.001):  # the exact end station can be refused
                try:
                    z = round(float(p.ElevationAt(ss)), 4)
                    break
                except Exception:
                    continue
            row.append(z)
        rows.append(row)
    names = [p.Name for p in profs]
    if out_txt:
        refuse_shared(out_txt)
        Path(out_txt).parent.mkdir(parents=True, exist_ok=True)
        with open(out_txt, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(" ".join("nan" if v is None else f"{v:.4f}" for v in r) + "\n")
        return {"written": out_txt, "columns": ["station"] + names, "rows": len(rows)}
    return {"columns": ["station"] + names, "rows": rows}


# ----------------------------------------------------------------------------- corridor surfaces
def rebuild_corridor_surfaces(c3d: Civil3D, match: str = "corridor") -> dict:
    """Rebuild every surface whose name contains `match` (corridor TOP / DATUM surfaces)."""
    c3d.guard_write()
    doc = civ.aecc_doc(c3d)
    done = []
    for s in _items(doc.Surfaces):
        name = s.Name
        if match.lower() not in name.lower():
            continue
        try:
            c3d._retry(lambda: s.Rebuild(), timeout=120)
            done.append({"surface": name, "ok": True})
        except Exception as e:  # noqa: BLE001  a referenced surface cannot be rebuilt here
            done.append({"surface": name, "ok": False, "note": str(e)[:100]})
    return {"rebuilt": done, "next": "Check the TOP surface against the profile, then c3d_save."}


# ----------------------------------------------------------------------------- text
def replace_text(c3d: Civil3D, old: str, new: str, dry_run: bool = False) -> dict:
    """Replace `old` with `new` in every text, mtext and attribute (model, layouts and block definitions)."""
    if not old:
        raise Civil3DError("Give the text to replace.")
    if not dry_run:
        c3d.guard_write()
    doc = c3d.doc()
    hits = []
    for b in _items(doc.Blocks):
        bname = _g(b, "Name", "")
        for e in _items(b):
            kind = _g(e, "ObjectName", "")
            if kind in ("AcDbMText", "AcDbText", "AcDbAttributeDefinition"):
                t = _g(e, "TextString", "")
                if t and old in t:
                    if not dry_run:
                        e.TextString = t.replace(old, new)
                    hits.append({"block": bname, "type": kind})
            elif kind == "AcDbBlockReference" and _g(e, "HasAttributes", False):
                try:
                    for a in e.GetAttributes():
                        t = a.TextString
                        if t and old in t:
                            if not dry_run:
                                a.TextString = t.replace(old, new)
                            hits.append({"block": bname, "type": "attribute", "tag": a.TagString})
                except Exception:
                    pass
    return {"old": old, "new": new, "count": len(hits), "where": hits[:40], "changed": not dry_run}


# ----------------------------------------------------------------------------- plotting from model space
def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def plot_model_window(c3d: Civil3D, x1: float, y1: float, x2: float, y2: float, pdf: str, scale: float = 200.0,
                      paper: str = "ISO full bleed A1 (841.00 x 594.00 MM)", ctb: str | None = None,
                      device: str = "DWG To PDF.pc3", landscape: bool = True) -> dict:
    """Plot a model-space window to a PDF at 1:`scale` (drawing units = metres, paper in mm), centred on the sheet.

    For pages of section views in a drawing with no sheet layouts. One window per call."""
    refuse_shared(pdf, "plot to")
    target = Path(pdf)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise Civil3DError(f"'{target}' exists. Plot to a new name (a PDF viewer may hold the old one open).")
    doc = c3d.doc()
    if ctb is None:
        ctb = "."
    w, h = abs(x2 - x1) * 1000.0 / scale, abs(y2 - y1) * 1000.0 / scale
    bp0 = doc.GetVariable("BACKGROUNDPLOT")
    t0 = time.time()
    try:
        doc.SetVariable("TILEMODE", 1)
        doc.SetVariable("BACKGROUNDPLOT", 0)
        cmd = ('(progn (command "_.-PLOT" "_Y" "Model" {dev} {paper} "_M" {orient} "_N" "_W" "{x1},{y1}" "{x2},{y2}" '
               '"1={u}" "_C" "_Y" {ctb} "_Y" "_A" {pdf} "_N" "_Y") (princ)) ').format(
            dev=_q(device), paper=_q(paper), orient='"_L"' if landscape else '"_P"',
            x1=min(x1, x2), y1=min(y1, y2), x2=max(x1, x2), y2=max(y1, y2), u=round(scale / 1000.0, 6),
            ctb=_q(ctb), pdf=_q(str(target)))
        res = None
        try:
            res = c3d.send(cmd, True, 40, label="plot model window")
        except Exception as e:  # noqa: BLE001  the Plot Job Progress window is reported as a pending dialog: wait it out
            res = {"note": type(e).__name__}
        end = time.time() + 45
        while not (target.exists() and target.stat().st_size > 0) and time.time() < end:
            time.sleep(0.3)
    finally:
        try:
            doc.SetVariable("BACKGROUNDPLOT", int(bp0))
        except Exception:
            pass
    ok = target.exists() and target.stat().st_size > 0
    out = {"pdf": str(target), "ok": ok, "plotted_mm": [round(w, 1), round(h, 1)], "scale": f"1:{scale:g}",
           "seconds": round(time.time() - t0, 1)}
    if not ok:
        out["send"] = res
        out["note"] = "No PDF appeared. Check the paper and plot-style names with road_plot_sheets(check_only=True)."
    return out


# ----------------------------------------------------------------------------- label groups and styles
def section_label_weeding(c3d: Civil3D, weeding: float = 0.3) -> dict:
    """Set the weeding distance of every section grade-break label group in model space (COM: WeedingValue)."""
    c3d.guard_write()
    n = ok = 0
    old = set()
    err = None
    for e in _items(c3d.ms()):
        if _g(e, "ObjectName", "") != "AeccDbSectionGradeBreakLabeling":
            continue
        n += 1
        try:
            old.add(round(float(e.WeedingValue), 3))
            e.WeedingValue = float(weeding)
            ok += 1
        except Exception as ex:  # noqa: BLE001
            err = str(ex)[:120]
    out = {"label_groups": n, "set": ok, "weeding": weeding, "was": sorted(old)}
    if n and ok < n:
        out["last_error"] = err
    out["next"] = "c3d_save, then plot and check each section has its centreline (0.000) label."
    return out


_STYLE_ROOTS = ("AlignmentLabelStyles", "ProfileLabelStyles", "ProfileViewLabelStyles", "SectionLabelStyles",
                "SectionViewLabelStyles", "PointLabelStyles", "SurfaceLabelStyles", "ParcelLabelStyles",
                "GeneralLineLabelStyles", "GeneralCurveLabelStyles", "GeneralNoteLabelStyles", "SampleLineLabelStyles")


def _style_collection(c3d: Civil3D, collection: str):
    """'ProfileLabelStyles.CurveLabelStyles' -> the COM collection."""
    o = civ.aecc_doc(c3d)
    for part in collection.split("."):
        try:
            o = getattr(o, part)
        except Exception:
            raise Civil3DError(f"No style collection '{collection}' (failed at '{part}'). Roots: {list(_STYLE_ROOTS)}; "
                               f"e.g. 'ProfileLabelStyles.CurveLabelStyles'.") from None
    return o


def _find_style(coll, name: str):
    for st in _items(coll):
        if st.Name == name:
            return st
    return None


def _components(st) -> list:
    return _items(_g(st, "TextComponents"))


def label_styles(c3d: Civil3D, collection: str, style: str | None = None) -> dict:
    """Label styles of a collection with their children; with `style` ('Parent' or 'Parent/Child'), its text components
    and their contents."""
    coll = _style_collection(c3d, collection)
    if not style:
        return {"collection": collection,
                "styles": [{"name": st.Name, "children": [c.Name for c in _items(_g(st, "Children"))]} for st in _items(coll)]}
    st = _resolve(coll, style, collection)
    return {"collection": collection, "style": style,
            "components": [{"name": t.Name, "contents": _g(_g(t, "Contents"), "Value"),
                            "height": _g(_g(t, "Height"), "Value")} for t in _components(st)]}


def _resolve(coll, style: str, collection: str):
    parts = [p.strip() for p in style.split("/")]
    st = _find_style(coll, parts[0])
    if st is None:
        raise Civil3DError(f"No style '{parts[0]}' in {collection}. It has: {[s.Name for s in _items(coll)]}")
    for child in parts[1:]:
        kids = _items(_g(st, "Children"))
        nxt = [k for k in kids if k.Name == child]
        if not nxt:
            raise Civil3DError(f"'{st.Name}' has no child style '{child}'. Children: {[k.Name for k in kids]}")
        st = nxt[0]
    return st


def set_label_style_text(c3d: Civil3D, collection: str, style: str, component: str, contents: str) -> dict:
    """Set the Contents of one text component of a label style (the text typed in the Text Component Editor).

    style: 'Parent' or 'Parent/Child' (e.g. 'Crest and Sag/Sag Only'). contents uses Civil 3D's field syntax,
    e.g. 'K:<[K Value(P2|RN|AP|Sn|OF)]>'. Read the component first with label_styles and keep its line-break form."""
    c3d.guard_write()
    coll = _style_collection(c3d, collection)
    st = _resolve(coll, style, collection)
    comps = _components(st)
    hit = [t for t in comps if t.Name == component]
    if not hit:
        raise Civil3DError(f"'{style}' has no text component '{component}'. It has: {[t.Name for t in comps]}")
    t = hit[0]
    old = _g(t.Contents, "Value")
    new = contents  # pass line breaks exactly as label_styles returns them for an existing component
    try:
        t.Contents.Value = new
    except Exception as e:  # noqa: BLE001
        raise Civil3DError(f"Could not set the contents: {str(e)[:150]}", {"old": old}) from None
    return {"style": style, "component": component, "old": old, "new": _g(t.Contents, "Value"),
            "next": "REGEN and c3d_save; plot one sheet and read the label before doing the rest."}
