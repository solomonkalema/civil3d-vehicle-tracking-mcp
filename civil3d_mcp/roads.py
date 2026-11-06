"""Road design production: read how a Civil 3D road project is built (v0.8).

Everything here reads; nothing writes. It answers the questions a reviewer asks of a
data-shortcut road project before touching it:
- which objects a drawing owns and which it takes by data reference (and whether those are stale or broken);
- how each corridor is built: baselines, regions, assemblies, frequency;
- what the production drawings hold: profile views, sample line groups, section views, view frames;
- how each sheet is set up: plotter, plot style, paper, viewport scales and the title-block attributes;
- which xrefs a drawing loads and where from;
- what the _Shortcuts folder of the project says on disk.

Walking ModelSpace one entity at a time over COM takes minutes on a survey drawing (tens of thousands of
COGO points and blocks). Type counts use filtered selection sets instead: a few seconds.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import civil as civ
from .com_bridge import Civil3D, Civil3DError, refuse_shared

_road: dict[str, object] = {}

# Model-space types worth counting in a road drawing
COUNT_TYPES = [
    "AECC_COGO_POINT", "AECC_PROFILE_VIEW", "AECC_SECTION_VIEW", "AECC_SAMPLE_LINE", "AECC_VIEW_FRAME",
    "AECC_MATCH_LINE", "AECC_SHEET", "AECC_FEATURE_LINE", "AECC_PIPE", "AECC_STRUCTURE",
    "INSERT", "LWPOLYLINE", "LINE", "TEXT", "MTEXT", "HATCH", "IMAGE", "DIMENSION", "MULTILEADER",
]

PARTS = ("alignments", "surfaces", "corridors", "xrefs", "layouts", "types")


def road_doc(c3d: Civil3D):
    """The Civil 3D roadway document (corridors, assemblies) for the active drawing."""
    civ.aecc_doc(c3d)  # probes the land API version first
    app = c3d.app()
    v = civ._cache.get("version")
    if _road.get("version") != v:
        _road["app"] = app.GetInterfaceObject(f"AeccXUiRoadway.AeccRoadwayApplication.{v}")
        _road["version"] = v
    try:
        return c3d._retry(lambda: _road["app"].ActiveDocument)
    except Exception:
        _road.clear()
        raise


def _g(o, name, default=None):
    try:
        return getattr(o, name)
    except Exception:
        return default


def _items(c) -> list:
    try:
        return [c.Item(i) for i in range(c.Count)]
    except Exception:
        return []


def _num(v):
    return round(v, 3) if isinstance(v, float) else v


def ref_state(o) -> str:
    """'own', 'ref', 'ref (stale)' or 'ref (BROKEN)' for a Civil 3D object."""
    try:
        if not o.IsReferenceObject():
            return "own"
        state = "ref"
        if not o.IsReferenceValid():
            state += " (BROKEN)"
        if o.IsReferenceStale():
            state += " (stale)"
        return state
    except Exception:
        return "?"


# ----------------------------------------------------------------------------- parts
def _alignments(ldoc, detail: bool) -> list[dict]:
    als = []
    for s in _items(ldoc.Sites):
        als += [(s.Name, a) for a in _items(s.Alignments)]
    als += [("(siteless)", a) for a in _items(ldoc.AlignmentsSiteless)]
    rows = []
    for site, a in als:
        r = {"name": a.Name, "src": ref_state(a), "style": _g(a, "StyleName"), "layer": _g(a, "Layer"),
             "length": round(float(_g(a, "Length", 0) or 0), 2)}
        if site != "(siteless)":
            r["site"] = site
        r["profiles"] = [
            {"name": p.Name, "kind": {1: "surface", 2: "layout", 3: "superimposed"}.get(_g(p, "Type"), _g(p, "Type")),
             "src": ref_state(p), "style": _g(p, "StyleName")}
            for p in _items(a.Profiles)
        ]
        pvs = _items(a.ProfileViews)
        if pvs:
            r["profile_views"] = [{"name": v.Name, "style": _g(v, "StyleName")} for v in pvs[:20]]
            if len(pvs) > 20:
                r["profile_view_count"] = len(pvs)
        groups = []
        for s in _items(a.SampleLineGroups):
            lines = _items(s.SampleLines)
            g = {"name": s.Name, "src": ref_state(s), "lines": len(lines)}
            if detail and lines:
                st = sorted(round(float(_g(x, "Station", 0) or 0), 2) for x in lines)
                gaps = sorted({round(b - a_, 2) for a_, b in zip(st, st[1:])})
                g.update({"from": st[0], "to": st[-1], "spacings": gaps[:6],
                          "section_views": sum((_g(_g(x, "SectionViews"), "Count", 0) or 0) for x in lines)})
            groups.append(g)
        if groups:
            r["sample_line_groups"] = groups
        rows.append(r)
    return rows


def _surfaces(ldoc) -> list[dict]:
    return [{"name": s.Name, "src": ref_state(s), "style": _g(s, "StyleName")} for s in _items(ldoc.Surfaces)]


def _corridors(rdoc) -> dict:
    cors = []
    for c in _items(rdoc.Corridors):
        cr = {"name": c.Name, "src": ref_state(c), "out_of_date": _g(c, "OutOfDate"), "baselines": []}
        for b in _items(c.Baselines):
            br = {"alignment": _g(_g(b, "Alignment"), "Name"), "profile": _g(_g(b, "Profile"), "Name"),
                  "from": _num(_g(b, "StartStation")), "to": _num(_g(b, "EndStation")), "regions": []}
            for rg in _items(_g(b, "BaselineRegions")):
                rr = {"from": _num(_g(rg, "StartStation")), "to": _num(_g(rg, "EndStation")),
                      "assembly": _g(_g(rg, "AssemblyDbEntity"), "Name")}
                try:
                    rr["stations"] = len(rg.GetSortedStations())
                except Exception:
                    pass
                n_off = _g(_g(rg, "OffsetBaselines"), "Count", 0)
                if n_off:
                    rr["offset_baselines"] = n_off
                br["regions"].append(rr)
            cr["baselines"].append(br)
        cr["surfaces"] = [s.Name for s in _items(_g(c, "CorridorSurfaces"))]
        cors.append(cr)
    out = {"corridors": cors}
    try:
        out["assemblies"] = [a.Name for a in _items(rdoc.Assemblies)]
    except Exception:
        out["assemblies"] = "not readable in this drawing (it holds no assemblies of its own)"
    return out


def _xrefs(doc) -> list[dict]:
    rows = []
    for b in _items(doc.Blocks):
        if _g(b, "IsXRef"):
            p = _g(b, "Path") or ""
            full = p if os.path.isabs(p) else os.path.normpath(os.path.join(os.path.dirname(doc.FullName), p))
            rows.append({"name": b.Name, "path": p, "found": os.path.exists(full)})
    return rows


def layouts(doc, attributes: bool = True) -> list[dict]:
    """Every paper-space layout: plot set-up, viewports (scale as paper mm per drawing metre) and blocks."""
    out = []
    for L in _items(doc.Layouts):
        if L.Name == "Model":
            continue
        vps, blocks = [], []
        for e in _items(_g(L, "Block")):
            on = _g(e, "ObjectName")
            if on == "AcDbViewport":
                sc = _g(e, "CustomScale")
                vps.append({"mm_per_m": _num(sc), "scale_if_mm_paper": f"1:{round(1000 / sc)}" if sc else None,
                            "w": round(_g(e, "Width", 0), 1), "h": round(_g(e, "Height", 0), 1)})
            elif on == "AcDbBlockReference" and attributes:
                b = {"name": _g(e, "EffectiveName") or _g(e, "Name"), "handle": _g(e, "Handle")}
                try:
                    if e.HasAttributes:
                        b["attrs"] = {a.TagString: a.TextString for a in e.GetAttributes() if a.TextString}
                except Exception:
                    pass
                blocks.append(b)
        row = {"name": L.Name, "tab": _g(L, "TabOrder"), "device": _g(L, "ConfigName"), "ctb": _g(L, "StyleSheet"),
               "paper": _g(L, "CanonicalMediaName"), "viewports": vps[1:]}  # the first viewport is the sheet itself
        if attributes:
            row["blocks"] = [b for b in blocks if b.get("attrs")] + [{"name": n} for n in sorted({b["name"] for b in blocks if not b.get("attrs")})]
        out.append(row)
    return sorted(out, key=lambda r: r.get("tab") or 0)


def type_counts(doc) -> dict:
    """Model-space counts per DXF type, through a filtered selection set per type."""
    import pythoncom
    import win32com.client

    out = {}
    name = "MCPROADS"
    try:
        doc.SelectionSets.Item(name).Delete()
    except Exception:
        pass
    ss = doc.SelectionSets.Add(name)
    try:
        codes = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [0, 410])
        for t in COUNT_TYPES:
            try:
                ss.Clear()
                vals = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, [t, "Model"])
                ss.Select(5, None, None, codes, vals)  # 5 = acSelectionSetAll
                if ss.Count:
                    out[t] = ss.Count
            except Exception as e:  # noqa: BLE001
                out[t] = f"? {e}"
    finally:
        ss.Delete()
    return out


# ----------------------------------------------------------------------------- entry points
def drawing_report(c3d: Civil3D, parts: list[str] | None = None, detail: bool = True) -> dict:
    """How the active drawing is built. parts: any of PARTS (default all)."""
    want = set(parts or PARTS)
    bad = want - set(PARTS)
    if bad:
        raise Civil3DError(f"Unknown part(s) {sorted(bad)}. Choose from {list(PARTS)}.")
    doc = c3d.doc()
    out = {"drawing": doc.FullName, "read_only": _g(doc, "ReadOnly"), "working_copy": c3d.is_working_copy(doc.FullName)}
    ldoc = civ.aecc_doc(c3d)
    if "alignments" in want:
        out["alignments"] = _alignments(ldoc, detail)
    if "surfaces" in want:
        out["surfaces"] = _surfaces(ldoc)
    if "corridors" in want:
        out.update(_corridors(road_doc(c3d)))
    if "xrefs" in want:
        out["xrefs"] = _xrefs(doc)
    if "layouts" in want:
        out["layouts"] = layouts(doc)
    if "types" in want:
        out["model_space_types"] = type_counts(doc)
    def _off(src: str) -> bool:
        return "stale" in src or "BROKEN" in src

    stale = []
    for a in out.get("alignments", []):
        if _off(a["src"]):
            stale.append(f"alignment {a['name']} [{a['src']}]")
        stale += [f"profile {a['name']} / {p['name']} [{p['src']}]" for p in a.get("profiles", []) if _off(p["src"])]
    stale += [f"surface {s['name']} [{s['src']}]" for s in out.get("surfaces", []) if _off(s["src"])]
    stale += [f"corridor {c['name']} [{c['src']}]" for c in out.get("corridors", []) if _off(c["src"])]
    if stale:
        out["needs_sync"] = stale
    return out


def report_file(c3d: Civil3D, path: str, parts: list[str] | None = None, detail: bool = True) -> dict:
    """Open `path` read-only (unless it is already open) and report on it. The drawing is left open.

    [observed 26 Oct 2026] Opening, reading and closing a data-shortcut drawing inside one step crashed Civil 3D
    (Access Violation) three times. The same reads with the close done in a later, separate step worked 8 of 8
    times. So this never closes: call c3d_close_drawing afterwards, as its own step."""
    import time

    app = c3d.app()
    docs = c3d._retry(lambda: app.Documents)
    already = None
    for i in range(c3d._retry(lambda: docs.Count)):
        d = docs.Item(i)
        # a drawing still opening or closing answers without FullName
        if os.path.normcase(_g(d, "FullName", "") or "") == os.path.normcase(path):
            already = d
    if already is not None:
        c3d._retry(lambda: already.Activate())
    else:
        if not os.path.exists(path):
            raise Civil3DError(f"No such drawing: {path}")
        c3d._retry(lambda: docs.Open(path, True), timeout=120)
        time.sleep(1.5)
    out = drawing_report(c3d, parts, detail)
    out["opened_by_this_call"] = already is None
    out["next"] = "Close it with c3d_close_drawing(path) as a separate step once you are done with it."
    return out


def close_drawing(c3d: Civil3D, path: str, force: bool = False) -> dict:
    """Close an open drawing without saving. Refuses a drawing with unsaved changes unless it is read-only.

    [observed 26 Oct 2026] COM closes of data-shortcut drawings crashed Civil 3D four times, once even as a
    separate step two minutes after opening. Without force this only explains that; leave the drawing open
    and restart Civil 3D between batches instead."""
    import gc
    import time

    if not force:
        return {"closed": False, "note": "Not closed: closing data-shortcut drawings over COM has crashed Civil 3D. "
                "Leave it open (read-only drawings lose nothing) and restart Civil 3D between batches. "
                "Pass force=True only for a plain drawing with no data references or xrefs."}

    app = c3d.app()
    docs = c3d._retry(lambda: app.Documents)
    target = None
    for i in range(c3d._retry(lambda: docs.Count)):
        d = docs.Item(i)
        if os.path.normcase(_g(d, "FullName", "") or "") == os.path.normcase(path):
            target = d
    if target is None:
        return {"closed": False, "note": f"not open: {path}"}
    read_only = bool(_g(target, "ReadOnly", False))
    if not read_only and not _g(target, "Saved", True):
        raise Civil3DError(f"{path} has unsaved changes. Save it (c3d_save) or say explicitly to discard them.")
    gc.collect()
    end = time.time() + 30
    while time.time() < end:
        try:
            if app.GetAcadState().IsQuiescent:
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(2)  # let background xref and data-reference loading settle
    c3d._retry(lambda: target.Close(False), timeout=60)
    del target
    gc.collect()
    return {"closed": True, "drawing": path, "was_read_only": read_only}


def shortcut_project(folder: str) -> dict:
    """Read a data-shortcut project folder: every shortcut, the drawing it points to, and whether that exists."""
    root = Path(folder)
    sc = root / "_Shortcuts"
    if not sc.is_dir():
        raise Civil3DError(f"No _Shortcuts folder in {folder}. Point at the project folder (the one holding _Shortcuts).")
    by_type: dict[str, list[dict]] = {}
    missing = []
    for sub in sorted(p for p in sc.iterdir() if p.is_dir()):
        for f in sorted(sub.glob("*.xml")):
            try:
                el = ET.parse(f).getroot().find(".//Shortcut")
                obj = el.find(".//Object")
                rel = el.find("DwgRelPath").get("path")
                abs_ = el.find(".//File").get("name")
            except Exception as e:  # noqa: BLE001
                by_type.setdefault(sub.name, []).append({"file": f.name, "error": str(e)})
                continue
            # DwgRelPath is relative to the working folder (the project's parent), e.g. '.\Roads Project\Source ...'
            src = Path(os.path.normpath(root.parent / rel)) if rel else Path(abs_)
            row = {"name": obj.get("name"), "source": os.path.relpath(src, root), "found": src.exists()}
            if abs_ and os.path.normcase(abs_) != os.path.normcase(str(src)):
                row["stored_path"] = abs_  # the absolute path Civil 3D saved (another PC's Drive path, say)
            if not row["found"]:
                missing.append(row)
            by_type.setdefault(sub.name, []).append(row)
    summary = {k: len(v) for k, v in by_type.items()}
    sources: dict[str, int] = {}
    for rows in by_type.values():
        for r in rows:
            if "source" in r:
                sources[r["source"]] = sources.get(r["source"], 0) + 1
    return {"project": str(root), "counts": summary, "source_drawings": dict(sorted(sources.items())),
            "missing_sources": missing, "shortcuts": by_type}


def pdf_page_png(pdf: str, page: int = 1, dpi: int = 60, out: str | None = None) -> str:
    """Render one page of a PDF (an issued sheet) to PNG so it can be looked at."""
    try:
        import pymupdf
    except ImportError as e:
        raise Civil3DError('PyMuPDF is missing. Run: "C:\\Program Files\\Python314\\python.exe" -m pip install --user pymupdf') from e
    d = pymupdf.open(pdf)
    if not 1 <= page <= len(d):
        raise Civil3DError(f"{pdf} has {len(d)} pages.")
    dst = out or str(Path(os.environ.get("TEMP", ".")) / f"{Path(pdf).stem}_p{page}.png")
    refuse_shared(dst)
    d[page - 1].get_pixmap(dpi=dpi).save(dst)
    return dst
