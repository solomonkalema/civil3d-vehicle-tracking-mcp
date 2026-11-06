"""Direction arrows and stage labels along swept paths, and bulk drawing through one LISP file.

Why this exists: on a TIA job (20 Oct 2026) the engineer asked for arrows along every run, green for
ingress and red for egress, so the developer can use the sheet as the basis of the signed circulation in
a split-level car park. Drawing them one COM call at a time is slow and was where the server choked, so
everything here is built as entmake data and sent to Civil 3D in one (load ...) per batch.

Pure geometry here; the server tools do the drawing.
"""

from __future__ import annotations

import math
from typing import Iterable

INGRESS = {"layer": "CIRC-IN", "color": 3}   # green
EGRESS = {"layer": "CIRC-OUT", "color": 1}   # red
STAGE = {"layer": "CIRC-STAGE", "color": 5}  # blue numbers in circles


def _length(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def point_at(pts: list[tuple[float, float]], s: float) -> tuple[float, float, float]:
    """(x, y, heading) at distance s along a polyline."""
    run = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if seg <= 1e-9:
            continue
        if run + seg >= s:
            t = (s - run) / seg
            return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), math.atan2(b[1] - a[1], b[0] - a[0])
        run += seg
    a, b = pts[-2], pts[-1]
    return b[0], b[1], math.atan2(b[1] - a[1], b[0] - a[0])


def arrows_along(points: Iterable[Iterable[float]], spacing: float = 5.0, size: float = 1.2,
                 start: float | None = None, reverse: bool = False) -> list[list[tuple[float, float]]]:
    """Filled arrowheads (triangles: tip, left, right) every `spacing` metres along a path.

    The arrow points in the direction of travel: the polyline's own direction, or the opposite with
    reverse=True (a guide drawn the other way round, or a reverse stage shown by its direction of motion).
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if reverse:
        pts = pts[::-1]
    if len(pts) < 2:
        raise ValueError("A path needs at least two points.")
    total = _length(pts)
    if total < size:
        return []
    s = spacing / 2 if start is None else start
    out = []
    half_w = size * 0.45
    while s <= total - size * 0.5:
        x, y, h = point_at(pts, s)
        tip = (x + size * 0.6 * math.cos(h), y + size * 0.6 * math.sin(h))
        bx, by = x - size * 0.4 * math.cos(h), y - size * 0.4 * math.sin(h)
        nx, ny = -math.sin(h), math.cos(h)
        out.append([tip, (bx + half_w * nx, by + half_w * ny), (bx - half_w * nx, by - half_w * ny)])
        s += spacing
    return out


def _lisp_num(v: float) -> str:
    return f"{v:.3f}"


def lisp_solids(triangles: list[list[tuple[float, float]]], layer: str, color: int) -> list[str]:
    """entmake forms for SOLID triangles (the 4th corner repeats the 3rd)."""
    forms = []
    for t in triangles:
        (x1, y1), (x2, y2), (x3, y3) = t
        forms.append(
            f'(entmake (list (cons 0 "SOLID") (cons 8 "{layer}") (cons 62 {int(color)})'
            f" (list 10 {_lisp_num(x1)} {_lisp_num(y1)} 0.0) (list 11 {_lisp_num(x2)} {_lisp_num(y2)} 0.0)"
            f" (list 12 {_lisp_num(x3)} {_lisp_num(y3)} 0.0) (list 13 {_lisp_num(x3)} {_lisp_num(y3)} 0.0)))"
        )
    return forms


def lisp_polyline(points: list[tuple[float, float]], layer: str, color: int | None = None, closed: bool = False) -> str:
    col = f" (cons 62 {int(color)})" if color is not None else ""
    verts = " ".join(f"(list 10 {_lisp_num(x)} {_lisp_num(y)})" for x, y in points)
    return (f'(entmake (list (cons 0 "LWPOLYLINE") (cons 100 "AcDbEntity") (cons 8 "{layer}"){col}'
            f' (cons 100 "AcDbPolyline") (cons 90 {len(points)}) (cons 70 {1 if closed else 0}) {verts}))')


def lisp_stage_label(x: float, y: float, number: int | str, radius: float, layer: str, color: int) -> list[str]:
    h = radius * 1.1
    return [
        f'(entmake (list (cons 0 "CIRCLE") (cons 8 "{layer}") (cons 62 {int(color)}) (list 10 {_lisp_num(x)} {_lisp_num(y)} 0.0) (cons 40 {_lisp_num(radius)})))',
        f'(entmake (list (cons 0 "TEXT") (cons 8 "{layer}") (cons 62 {int(color)}) (list 10 {_lisp_num(x)} {_lisp_num(y)} 0.0)'
        f' (list 11 {_lisp_num(x)} {_lisp_num(y)} 0.0) (cons 40 {_lisp_num(h)}) (cons 1 "{number}") (cons 72 1) (cons 73 2)))',
    ]


def batch(forms: list[str], max_chars: int = 60000) -> list[str]:
    """Group entmake forms into (progn ...) batches that each return the number of entities made."""
    out, cur, size = [], [], 0
    for f in forms:
        if cur and size + len(f) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += len(f)
    if cur:
        out.append(cur)
    return ["(progn (setq mcp:n 0) " + " ".join(f"(if {f} (setq mcp:n (1+ mcp:n)))" for f in b) + " mcp:n)" for b in out]
