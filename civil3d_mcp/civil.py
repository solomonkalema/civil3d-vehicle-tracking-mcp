"""Civil 3D object access (alignments, profiles, surfaces, corridors, points).

The Civil 3D COM API lives in a separate type library (AeccXUiLand) whose
ProgID carries a version number that changes each release. We probe a range
rather than hard-coding one release. The AutoCAD-level fallback (selection by
DXF name) works on any version and is used for inventories.

Note: Autodesk's Civil 3D COM API is thinner than its .NET API. Anything this
module cannot reach (corridor rebuilds with targets, assembly editing) is a
candidate for a later C# plugin; the MCP layout leaves room for that.
"""

from __future__ import annotations

from .com_bridge import Civil3D, Civil3DError

# 13.x covers Civil 3D 2020 onwards; 14.x/15.x left in for future releases.
_VERSIONS = [f"{major}.{minor}" for major in (15, 14, 13) for minor in range(9, -1, -1)]

DXF_NAMES = {
    "alignments": "AECC_ALIGNMENT",
    "profiles": "AECC_PROFILE",
    "surfaces": "AECC_TIN_SURFACE,AECC_GRID_SURFACE",
    "corridors": "AECC_CORRIDOR",
    "points": "AECC_COGO_POINT",
    "feature_lines": "AECC_FEATURE_LINE",
    "pipe_networks": "AECC_PIPE,AECC_STRUCTURE",
}

_cache: dict[str, object] = {}


def aecc_doc(c3d: Civil3D):
    """The Civil 3D (Aecc) document object for the active drawing."""
    app = c3d.app()
    if "app" not in _cache:
        last = None
        for v in _VERSIONS:
            try:
                _cache["app"] = app.GetInterfaceObject(f"AeccXUiLand.AeccApplication.{v}")
                _cache["version"] = v
                break
            except Exception as e:  # noqa: BLE001
                last = e
        else:
            raise Civil3DError(f"Could not reach the Civil 3D COM API (AeccXUiLand). Last error: {last}")
    try:
        return c3d._retry(lambda: _cache["app"].ActiveDocument)
    except Exception:
        _cache.clear()  # Civil 3D was restarted; probe again once
        return aecc_doc(c3d)


def inventory(c3d: Civil3D) -> dict:
    """Counts and names of Civil objects in model space (version-independent)."""
    out = {}
    for key, dxf in DXF_NAMES.items():
        # One in-process LISP read per object type: count plus up to 200 names
        # (handle when an object has no Name).
        count, names = c3d.lisp_data(
            f'(progn (setq mcp:ss (ssget "_X" (list (cons 0 "{dxf}") (cons 410 "Model"))) mcp:n nil mcp:i 0)'
            " (if mcp:ss (repeat (min (sslength mcp:ss) 200)"
            " (setq mcp:e (ssname mcp:ss mcp:i) mcp:i (1+ mcp:i)"
            " mcp:v (vl-catch-all-apply (quote vla-get-name) (list (vlax-ename->vla-object mcp:e)))"
            " mcp:n (cons (if (vl-catch-all-error-p mcp:v) (cdr (assoc 5 (entget mcp:e))) mcp:v) mcp:n))))"
            " (list (if mcp:ss (sslength mcp:ss) 0) (reverse mcp:n)))"
        )
        out[key] = {"count": count, "names": names or []}
    out["aecc_api_version"] = _cache.get("version", "not probed yet")
    return out


def _alignments(c3d: Civil3D) -> list:
    doc = aecc_doc(c3d)
    found = []
    try:
        al = doc.AlignmentsSiteless
        found.extend(al.Item(i) for i in range(al.Count))
    except Exception:
        pass
    try:
        sites = doc.Sites
        for i in range(sites.Count):
            al = sites.Item(i).Alignments
            found.extend(al.Item(j) for j in range(al.Count))
    except Exception:
        pass
    return found


def get_alignment(c3d: Civil3D, name: str):
    for a in _alignments(c3d):
        if a.Name == name:
            return a
    raise Civil3DError(f"No alignment named '{name}'. Use civil_inventory to list them.")


def alignment_summary(c3d: Civil3D) -> list[dict]:
    rows = []
    for a in _alignments(c3d):
        row = {"name": a.Name, "handle": a.Handle}
        for prop in ("Length", "StartingStation", "EndingStation"):
            try:
                row[prop] = round(float(getattr(a, prop)), 3)
            except Exception:
                pass
        try:
            row["profiles"] = [a.Profiles.Item(i).Name for i in range(a.Profiles.Count)]
        except Exception:
            pass
        rows.append(row)
    return rows


def point_at_station(c3d: Civil3D, alignment: str, station: float, offset: float = 0.0) -> dict:
    import pythoncom

    a = get_alignment(c3d, alignment)
    e, n = c3d.out_var(pythoncom.VT_R8), c3d.out_var(pythoncom.VT_R8)
    c3d._retry(lambda: a.PointLocation(station, offset, e, n))
    return {"alignment": alignment, "station": station, "offset": offset, "easting": round(e.value, 3), "northing": round(n.value, 3)}


def station_offset(c3d: Civil3D, alignment: str, x: float, y: float) -> dict:
    import pythoncom

    a = get_alignment(c3d, alignment)
    st, off = c3d.out_var(pythoncom.VT_R8), c3d.out_var(pythoncom.VT_R8)
    c3d._retry(lambda: a.StationOffset(x, y, st, off))
    return {"alignment": alignment, "x": x, "y": y, "station": round(st.value, 3), "offset": round(off.value, 3)}


def profile_elevations(c3d: Civil3D, alignment: str, profile: str, stations: list[float]) -> dict:
    a = get_alignment(c3d, alignment)
    prof = None
    for i in range(a.Profiles.Count):
        if a.Profiles.Item(i).Name == profile:
            prof = a.Profiles.Item(i)
    if prof is None:
        raise Civil3DError(f"Alignment '{alignment}' has no profile '{profile}'.")
    rows = []
    for s in stations:
        try:
            rows.append({"station": s, "elevation": round(float(c3d._retry(lambda s=s: prof.ElevationAt(s))), 3)})
        except Exception as e:  # noqa: BLE001
            rows.append({"station": s, "error": str(e)})
    return {"alignment": alignment, "profile": profile, "rows": rows}


def surface_elevation(c3d: Civil3D, surface: str, points: list[tuple[float, float]]) -> dict:
    doc = aecc_doc(c3d)
    surfs = doc.Surfaces
    s = None
    for i in range(surfs.Count):
        if surfs.Item(i).Name == surface:
            s = surfs.Item(i)
    if s is None:
        raise Civil3DError(f"No surface named '{surface}'.")
    rows = []
    for x, y in points:
        try:
            rows.append({"x": x, "y": y, "z": round(float(c3d._retry(lambda x=x, y=y: s.FindElevationAtXY(x, y))), 3)})
        except Exception:
            rows.append({"x": x, "y": y, "z": None, "note": "outside surface"})
    return {"surface": surface, "rows": rows}


def alignment_from_polyline(c3d: Civil3D, polyline_handle: str, name: str, layer: str = "C-ROAD-ALIGN") -> dict:
    """Create a siteless alignment from a polyline, using default styles."""
    c3d.guard_write()
    doc = aecc_doc(c3d)
    c3d.ensure_layer(layer)
    pl = c3d.entity(polyline_handle)
    al = doc.AlignmentsSiteless
    style = doc.AlignmentStyles.Item(0)
    label = doc.AlignmentLabelStyleSets.Item(0)
    a = c3d._retry(lambda: al.AddFromPolyline(name, layer, pl.ObjectID, style, label, False, True))
    return {"alignment": a.Name, "handle": a.Handle, "length": round(float(a.Length), 3)}
