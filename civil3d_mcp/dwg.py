"""Read drawing geometry over COM and turn it into shapely objects.

Used to pull walls, kerbs, boundaries, bays and guide lines out of a DWG so the
swept path engine can check clearances against real linework instead of a raster.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon

from .com_bridge import Civil3D
from .geometry import Obstacle, arc_points, bulge_points

SUPPORTED = "LINE,LWPOLYLINE,POLYLINE,ARC,CIRCLE,SPLINE,INSERT"


def _xy_pairs(flat, stride: int) -> list[tuple[float, float]]:
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), stride)]


def entity_points(c3d: Civil3D, ent, depth: int = 0) -> list[tuple[list[tuple[float, float]], bool, str]]:
    """Return [(points, closed, kind)] for one entity. Blocks are exploded on a copy."""
    r = c3d._retry
    kind = r(lambda: ent.ObjectName)
    if kind == "AcDbLine":
        s, e = r(lambda: ent.StartPoint), r(lambda: ent.EndPoint)
        return [([(s[0], s[1]), (e[0], e[1])], False, kind)]
    if kind == "AcDbPolyline":
        coords = r(lambda: ent.Coordinates)
        verts = _xy_pairs(coords, 2)
        closed = bool(r(lambda: ent.Closed))
        pts = [verts[0]]
        n = len(verts)
        segs = n if closed else n - 1
        for i in range(segs):
            b = r(lambda i=i: ent.GetBulge(i))
            pts.extend(bulge_points(verts[i], verts[(i + 1) % n], b))
        return [(pts, closed, kind)]
    if kind in ("AcDb2dPolyline", "AcDb3dPolyline"):
        coords = r(lambda: ent.Coordinates)
        return [(_xy_pairs(coords, 3), bool(r(lambda: ent.Closed)), kind)]
    if kind == "AcDbArc":
        c = r(lambda: ent.Center)
        pts = arc_points(c[0], c[1], r(lambda: ent.Radius), r(lambda: ent.StartAngle), r(lambda: ent.EndAngle), ccw=True)
        return [(pts, False, kind)]
    if kind == "AcDbCircle":
        c = r(lambda: ent.Center)
        pts = arc_points(c[0], c[1], r(lambda: ent.Radius), 0, 2 * math.pi - 1e-9, ccw=True)
        return [(pts, True, kind)]
    if kind == "AcDbSpline":
        try:
            fp = r(lambda: ent.FitPoints)
            pts = _xy_pairs(fp, 3) if len(fp) >= 6 else []
        except Exception:
            pts = []
        if not pts:
            pts = _xy_pairs(r(lambda: ent.ControlPoints), 3)  # approximation
        return [(pts, bool(r(lambda: ent.Closed)), kind + " (approx)")]
    if kind == "AcDbBlockReference" and depth < 3:
        out = []
        try:
            parts = r(lambda: ent.Explode())  # creates new entities; delete them after reading
        except Exception:  # xref, locked layer or non-explodable block: skip it
            return []
        try:
            for p in parts:
                out.extend(entity_points(c3d, p, depth + 1))
        finally:
            for p in parts:
                try:
                    p.Delete()
                except Exception:
                    pass
        return out
    return []


def read_layers(c3d: Civil3D, layer_pattern: str, as_obstacles: bool = True, min_area_closed: float = 0.0) -> list[Obstacle]:
    """Geometry on matching layers. Closed shapes become polygons, the rest lines."""
    ents = c3d.select_on_layers(layer_pattern, SUPPORTED)
    out: list[Obstacle] = []
    for e in ents:
        layer = c3d._retry(lambda e=e: e.Layer)
        handle = c3d._retry(lambda e=e: e.Handle)
        for pts, closed, kind in entity_points(c3d, e):
            if len(pts) < 2:
                continue
            label = f"{layer}:{handle} ({kind})"
            if closed and len(pts) >= 3:
                poly = Polygon(pts).buffer(0)
                if poly.area > min_area_closed:
                    # obstacles are checked against the outline, so a car inside a bay outline is not "in conflict"
                    out.append(Obstacle(poly.exterior if as_obstacles else poly, label))
                    continue
            out.append(Obstacle(LineString(pts), label))
    return out


def polyline_points(c3d: Civil3D, handle: str) -> list[tuple[float, float]]:
    ent = c3d.entity(handle)
    parts = entity_points(c3d, ent)
    if not parts:
        raise ValueError(f"Entity {handle} is not a line, polyline, arc or spline")
    return parts[0][0]


def layer_inventory(c3d: Civil3D) -> list[dict]:
    """Layers with entity counts in model space (quick picture of an unfamiliar DWG)."""
    pairs = c3d.lisp_data(
        '(progn (setq mcp:ss (ssget "_X" (list (cons 410 "Model"))) mcp:c nil mcp:i 0)'
        " (if mcp:ss (repeat (sslength mcp:ss)"
        " (setq mcp:l (cdr (assoc 8 (entget (ssname mcp:ss mcp:i)))) mcp:i (1+ mcp:i))"
        " (if (setq mcp:p (assoc mcp:l mcp:c)) (setq mcp:c (subst (cons mcp:l (1+ (cdr mcp:p))) mcp:p mcp:c))"
        " (setq mcp:c (cons (cons mcp:l 1) mcp:c))))) mcp:c)"
    ) or []
    counts: dict[str, int] = {name: n for name, n in pairs}
    rows = []
    for lay in c3d.layers():
        rows.append({**lay, "entities": counts.get(lay["name"], 0)})
    return sorted(rows, key=lambda r: -r["entities"])
