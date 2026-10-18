"""Ramp check in section: does a vehicle pass along a ramp without grounding or striking the soffit?

Plan swept paths (geometry.py, Vehicle Tracking) say nothing about levels. On a ramp to a basement or a
podium a vehicle can fit in plan and still scrape its underside at a crest (breakover), its front
overhang at the foot of a ramp (approach), its rear overhang at the top (departure), or hit a beam or
slab soffit with its roof. This module checks those four things in the long section.

Model (2D, in the plane of the ramp's centre line):
- floor: polyline of (chainage, level); soffit (optional): polyline of (chainage, level) for the
  underside of slabs, beams, ducts or a door head.
- the vehicle is a rigid side profile: both axles' tyres touch the floor, the underbody is a line
  ground_clearance above the chord between the two contact points, the overhang undersides are
  overhang_clearance above it, and the roof is body_height above it.
- wheel radius is ignored (contact = floor point), which is slightly conservative at crests.
- every position is checked with the vehicle facing up-chainage and facing down-chainage, so driving
  in forwards and out forwards are both covered.

Clearances are vertical (floor level under the body point, or soffit level above it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Pt = tuple[float, float]

# Indicative side-profile values. SU figures are the SANRAL SU values shown on the Vehicle Tracking
# profile (Min Body Ground Clearance 0.427 m, Overall Body Height 3.632 m); P likewise (0.305 m, 1.520 m).
# LGV values are typical:
# replace them with the Vehicle Tracking profile figures of the vehicle actually used.
SIDE_DEFAULTS: dict[str, dict] = {
    "P": {"ground_clearance": 0.305, "overhang_clearance": 0.305, "body_height": 1.52,
          "source": "SANRAL P, Vehicle Tracking profile (real low cars sit nearer 0.12 m: also check that)"},
    "SU": {"ground_clearance": 0.427, "overhang_clearance": 0.427, "body_height": 3.632,
           "source": "SANRAL SU, Vehicle Tracking profile"},
    "LGV": {"ground_clearance": 0.18, "overhang_clearance": 0.20, "body_height": 2.60,
            "source": "indicative light goods vehicle; check against the VT profile"},
}


@dataclass
class SideProfile:
    name: str
    wheelbase: float
    front_overhang: float
    rear_overhang: float
    ground_clearance: float
    overhang_clearance: float
    body_height: float
    source: str = ""


def _interp(poly: list[Pt], x: float) -> float | None:
    if x < poly[0][0] - 1e-9 or x > poly[-1][0] + 1e-9:
        return None
    for (x0, z0), (x1, z1) in zip(poly, poly[1:]):
        if x0 - 1e-9 <= x <= x1 + 1e-9:
            if x1 == x0:
                return max(z0, z1)
            return z0 + (z1 - z0) * (x - x0) / (x1 - x0)
    return None


def _check_poly(poly: list[Pt], what: str) -> list[Pt]:
    pts = [(float(a), float(b)) for a, b in poly]
    if len(pts) < 2:
        raise ValueError(f"{what}: need at least two points (chainage, level)")
    for (a, _), (b, _) in zip(pts, pts[1:]):
        if b <= a:
            raise ValueError(f"{what}: chainage must increase along the polyline ({a} then {b})")
    return pts


def profile_report(floor: list[Pt], soffit: list[Pt] | None = None, step: float = 0.25) -> dict:
    """Grades, grade changes and static headroom of the ramp itself."""
    grades = []
    for (x0, z0), (x1, z1) in zip(floor, floor[1:]):
        grades.append({"from_ch": round(x0, 3), "to_ch": round(x1, 3), "length_m": round(x1 - x0, 3),
                       "grade_pct": round(100 * (z1 - z0) / (x1 - x0), 2)})
    changes = []
    for i in range(1, len(grades)):
        changes.append({"at_ch": grades[i]["from_ch"],
                        "change_pct": round(grades[i]["grade_pct"] - grades[i - 1]["grade_pct"], 2)})
    out = {"segments": grades,
           "max_grade_pct": max(abs(g["grade_pct"]) for g in grades),
           "grade_changes": changes}
    if soffit:
        lo, hi = max(floor[0][0], soffit[0][0]), min(floor[-1][0], soffit[-1][0])
        best = None
        x = lo
        while x <= hi + 1e-9:
            h = _interp(soffit, x) - _interp(floor, x)
            if best is None or h < best[1]:
                best = (x, h)
            x += step
        if best:
            out["min_static_headroom_m"] = round(best[1], 3)
            out["min_static_headroom_at_ch"] = round(best[0], 3)
    return out


def _rear_contact(floor: list[Pt], xf: float, wb: float) -> Pt | None:
    """Rear tyre contact behind a front contact at chainage xf, wheelbase wb along the chord."""
    zf = _interp(floor, xf)
    lo, hi = xf - wb, xf  # horizontal distance <= chord
    if lo < floor[0][0]:
        return None
    f = lambda xr: math.hypot(xf - xr, zf - _interp(floor, xr)) - wb  # f(lo) >= 0 >= f(hi)
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    xr = (lo + hi) / 2
    return xr, _interp(floor, xr)


def _poses(floor: list[Pt], v: SideProfile, step: float):
    """Yield (rear_contact, front_contact) with the vehicle facing up-chainage."""
    x = floor[0][0] + v.wheelbase
    while x <= floor[-1][0] + 1e-9:
        rc = _rear_contact(floor, x, v.wheelbase)
        if rc is not None:
            yield rc, (x, _interp(floor, x))
        x += step


def _mirror(poly: list[Pt]) -> list[Pt]:
    end = poly[-1][0]
    return [(end - x, z) for x, z in reversed(poly)]


def _body_points(v: SideProfile, rc: Pt, fc: Pt, du: float = 0.05):
    ex, ez = (fc[0] - rc[0]) / v.wheelbase, (fc[1] - rc[1]) / v.wheelbase
    nx, nz = -ez, ex  # up-normal (ex > 0)
    u = -v.rear_overhang
    end = v.wheelbase + v.front_overhang
    while u <= end + 1e-9:
        if u < 0:
            h, part = v.overhang_clearance, "rear overhang (departure)"
        elif u > v.wheelbase:
            h, part = v.overhang_clearance, "front overhang (approach)"
        else:
            h, part = v.ground_clearance, "underbody between axles (breakover)"
        under = (rc[0] + u * ex + h * nx, rc[1] + u * ez + h * nz)
        roof = (rc[0] + u * ex + v.body_height * nx, rc[1] + u * ez + v.body_height * nz)
        yield u, part, under, roof
        u += du


def outline(v: SideProfile, rc: Pt, fc: Pt) -> list[Pt]:
    """Closed side outline (underside and roof) of the vehicle at one pose, for drawing."""
    pts = list(_body_points(v, rc, fc, du=0.1))
    bottom = [p[2] for p in pts]
    top = [p[3] for p in reversed(pts)]
    return bottom + top + [bottom[0]]


def ramp_check(v: SideProfile, floor: list[Pt], soffit: list[Pt] | None = None, step: float = 0.1,
               required_underbody_m: float = 0.05, required_headroom_m: float = 0.10,
               orientations: str = "both") -> dict:
    floor = _check_poly(floor, "floor")
    soffit = _check_poly(soffit, "soffit") if soffit else None
    runs = []
    if orientations in ("both", "up"):
        runs.append(("facing up-chainage", floor, soffit, False))
    if orientations in ("both", "down"):
        runs.append(("facing down-chainage", _mirror(floor), _mirror(soffit) if soffit else None, True))
    end = floor[-1][0]
    worst_under = None
    worst_head = None
    hits_under, hits_head = [], []
    n_poses = 0
    for label, fl, so, mirrored in runs:
        for rc, fc in _poses(fl, v, step):
            n_poses += 1
            pose_min_u, pose_min_h = None, None
            for u, part, under, roof in _body_points(v, rc, fc):
                zf = _interp(fl, under[0])
                if zf is not None:
                    c = under[1] - zf
                    if pose_min_u is None or c < pose_min_u[0]:
                        pose_min_u = (c, part, under[0])
                if so is not None:
                    zs = _interp(so, roof[0])
                    if zs is not None:
                        c = zs - roof[1]
                        if pose_min_h is None or c < pose_min_h[0]:
                            pose_min_h = (c, roof[0])
            def real(x):  # back to the drawing's chainage
                return end - x if mirrored else x
            def pose_real():
                if not mirrored:
                    return rc, fc
                return (end - rc[0], rc[1]), (end - fc[0], fc[1])
            if pose_min_u:
                rec = {"clearance_m": round(pose_min_u[0], 3), "part": pose_min_u[1],
                       "at_ch": round(real(pose_min_u[2]), 3), "orientation": label,
                       "front_axle_ch": round(real(fc[0]), 3)}
                if worst_under is None or pose_min_u[0] < worst_under[0]["clearance_m"]:
                    worst_under = (rec, pose_real())
                if pose_min_u[0] < required_underbody_m:
                    hits_under.append(rec)
            if pose_min_h:
                rec = {"clearance_m": round(pose_min_h[0], 3), "at_ch": round(real(pose_min_h[1]), 3),
                       "orientation": label, "front_axle_ch": round(real(fc[0]), 3)}
                if worst_head is None or pose_min_h[0] < worst_head[0]["clearance_m"]:
                    worst_head = (rec, pose_real())
                if pose_min_h[0] < required_headroom_m:
                    hits_head.append(rec)
    if not n_poses:
        raise ValueError("The floor polyline is shorter than the wheelbase: nothing to check.")

    def summarise(hits):  # group consecutive failures into chainage ranges, one per part/orientation
        groups: dict[tuple, list[float]] = {}
        for h in hits:
            key = (h.get("part", "roof"), h["orientation"])
            groups.setdefault(key, []).append(h["at_ch"])
        return [{"part": k[0], "orientation": k[1], "from_ch": min(v_), "to_ch": max(v_)}
                for k, v_ in groups.items()]

    out = {
        "vehicle": v.name,
        "side_profile": {"wheelbase": v.wheelbase, "front_overhang": v.front_overhang,
                         "rear_overhang": v.rear_overhang, "ground_clearance": v.ground_clearance,
                         "overhang_clearance": v.overhang_clearance, "body_height": v.body_height,
                         "source": v.source},
        "poses_checked": n_poses,
        "ramp": profile_report(floor, soffit),
        "underbody": {"required_m": required_underbody_m, "min": worst_under[0] if worst_under else None,
                      "passes": not hits_under, "failures": summarise(hits_under)},
    }
    if soffit:
        out["headroom"] = {"required_m": required_headroom_m, "min": worst_head[0] if worst_head else None,
                           "passes": not hits_head, "failures": summarise(hits_head)}
    out["passes"] = out["underbody"]["passes"] and (out.get("headroom", {}).get("passes", True))
    out["_worst_poses"] = {"underbody": worst_under[1] if worst_under else None,
                           "headroom": worst_head[1] if worst_head else None}
    return out


def render_png(path: str, v: SideProfile, floor: list[Pt], soffit: list[Pt] | None, result: dict,
               title: str = "", px_per_m: float = 60.0) -> str:
    """Colour section figure with a scale bar: floor, soffit, the vehicle at its critical positions."""
    from PIL import Image, ImageDraw, ImageFont

    pts = list(floor) + list(soffit or [])
    poses = [p for p in result["_worst_poses"].values() if p]
    for rc, fc in poses:
        pts += outline(v, rc, fc)
    xmin, xmax = min(p[0] for p in pts) - 1, max(p[0] for p in pts) + 1
    zmin, zmax = min(p[1] for p in pts) - 1, max(p[1] for p in pts) + 1.5
    W = int((xmax - xmin) * px_per_m) + 40
    H = int((zmax - zmin) * px_per_m) + 170
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        bold = ImageFont.truetype("arialbd.ttf", 16)
    except OSError:
        font = bold = ImageFont.load_default()
    X = lambda x: 20 + (x - xmin) * px_per_m
    Z = lambda z: 40 + (zmax - z) * px_per_m
    d.line([(X(x), Z(z)) for x, z in floor], fill=(60, 60, 60), width=4)
    if soffit:
        d.line([(X(x), Z(z)) for x, z in soffit], fill=(90, 90, 200), width=3)
    colours = {"underbody": (224, 123, 0), "headroom": (200, 30, 30)}
    for key, pose in result["_worst_poses"].items():
        if pose:
            rc, fc = pose
            d.line([(X(x), Z(z)) for x, z in outline(v, rc, fc)], fill=colours[key], width=2)
            for c in (rc, fc):
                r = 0.3 * px_per_m
                d.ellipse([X(c[0]) - r, Z(c[1]) - 2 * r, X(c[0]) + r, Z(c[1])], outline=(0, 0, 0), width=2)
    d.text((20, 8), title or f"Ramp check in section: {v.name}", fill="black", font=bold)
    y = H - 120
    for i in range(5):  # 5 m scale bar
        d.rectangle([X(xmin) + i * px_per_m, y, X(xmin) + (i + 1) * px_per_m, y + 8],
                    fill="black" if i % 2 == 0 else "white", outline="black")
    d.text((X(xmin), y + 12), "0", fill="black", font=font)
    d.text((X(xmin) + 5 * px_per_m - 20, y + 12), "5 m", fill="black", font=font)
    lines = []
    ub = result["underbody"]["min"]
    if ub:
        lines.append(f"Underside: min {ub['clearance_m']:.2f} m at ch {ub['at_ch']:.1f} ({ub['part']}, "
                     f"{ub['orientation']}); required {result['underbody']['required_m']:.2f} m (orange)")
    if result.get("headroom") and result["headroom"]["min"]:
        hr = result["headroom"]["min"]
        lines.append(f"Roof to soffit: min {hr['clearance_m']:.2f} m at ch {hr['at_ch']:.1f}; "
                     f"required {result['headroom']['required_m']:.2f} m (red)")
    lines.append(f"Max grade {result['ramp']['max_grade_pct']:.1f}%. Natural scale, no vertical exaggeration. "
                 f"Profile: {v.source}")
    for i, t in enumerate(lines):
        d.text((X(xmin) + 6 * px_per_m, y - 4 + 18 * i), t, fill="black", font=font)
    im.save(path)
    return path
