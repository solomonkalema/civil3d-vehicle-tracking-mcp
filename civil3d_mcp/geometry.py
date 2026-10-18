"""Pure-Python geometry: guide paths, vehicle kinematics, swept envelopes, clearance.

No Civil 3D dependency, so everything here can be tested on any machine.

Conventions
-----------
* Coordinates are drawing metres, angles in radians unless a name ends in _deg.
* A "pose" is the rear-axle centre (x, y) and body heading theta of the tractor
  (or rigid vehicle), plus the trailer axle pose for articulated vehicles.
* reference="rear_axle": the guide line is the path of the rear axle. This is
  exact for both forward and reverse runs and matches how the _rev guide lines
  are drawn for Vehicle Tracking.
* reference="front_axle": the guide line is the path of the steered axle; the
  rear axle follows it as a tractrix. Forward runs only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from .vehicles import Vehicle

Pt = tuple[float, float]


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------
def _unit(dx: float, dy: float) -> Pt:
    n = math.hypot(dx, dy)
    if n == 0:
        raise ValueError("zero-length segment")
    return dx / n, dy / n


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def arc_points(cx: float, cy: float, r: float, a0: float, a1: float, ccw: bool, step: float = 0.25) -> list[Pt]:
    """Points along a circular arc from angle a0 to a1 (inclusive)."""
    sweep = (a1 - a0) % (2 * math.pi)
    if not ccw:
        sweep = sweep - 2 * math.pi if sweep != 0 else 0.0
    n = max(2, int(abs(sweep) * r / step) + 1)
    return [(cx + r * math.cos(a0 + sweep * i / (n - 1)), cy + r * math.sin(a0 + sweep * i / (n - 1))) for i in range(n)]


def bulge_points(p0: Pt, p1: Pt, bulge: float, step: float = 0.25) -> list[Pt]:
    """Expand an LWPOLYLINE bulge segment into points (p0 excluded, p1 included)."""
    if abs(bulge) < 1e-9:
        return [p1]
    chord = math.dist(p0, p1)
    theta = 4 * math.atan(bulge)  # included angle, signed (+ = CCW)
    r = chord / (2 * math.sin(abs(theta) / 2))
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    ux, uy = _unit(p1[0] - p0[0], p1[1] - p0[1])
    # distance from chord midpoint to centre
    h = r * math.cos(abs(theta) / 2)
    side = 1 if bulge > 0 else -1
    cx, cy = mx - side * uy * h, my + side * ux * h
    if abs(theta) > math.pi:  # major arc: centre on the other side
        cx, cy = mx + side * uy * h, my - side * ux * h
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    pts = arc_points(cx, cy, r, a0, a1, ccw=bulge > 0, step=step)
    pts[-1] = p1
    return pts[1:]


# ---------------------------------------------------------------------------
# Guide path construction
# ---------------------------------------------------------------------------
@dataclass
class PathSample:
    x: float
    y: float
    heading: float  # direction of travel along the path
    curvature: float  # signed, + = turning left (CCW)
    s: float  # distance along path


def fillet_path(vertices: list[Pt], radii: list[float] | float, build_up: float = 0.0, step: float = 0.25) -> list[Pt]:
    """Straight legs joined by circular fillets.

    vertices: corner points of the guide line (first and last are the ends).
    radii: one radius per interior vertex, or a single value for all.
    build_up: length of a clothoid-like transition approximated by easing the
        fillet in over this distance (0 = plain arcs). Use 2-4 m before
        full-lock arcs so Vehicle Tracking Follow does not overshoot.
    Returns a dense point list.
    """
    if len(vertices) < 2:
        raise ValueError("need at least two vertices")
    n_int = len(vertices) - 2
    if isinstance(radii, (int, float)):
        radii = [float(radii)] * n_int
    if len(radii) != n_int:
        raise ValueError(f"expected {n_int} radii, got {len(radii)}")

    # tangent length each fillet takes from its two legs (0 for a straight-through or radius-0 vertex)
    tans = [0.0] * len(vertices)
    for i in range(1, len(vertices) - 1):
        a, b, c = vertices[i - 1], vertices[i], vertices[i + 1]
        u1 = _unit(b[0] - a[0], b[1] - a[1])
        u2 = _unit(c[0] - b[0], c[1] - b[1])
        turn = wrap(math.atan2(u2[1], u2[0]) - math.atan2(u1[1], u1[0]))
        if abs(turn) >= 1e-6 and radii[i - 1] > 0:
            tans[i] = radii[i - 1] * math.tan(abs(turn) / 2)

    out: list[Pt] = [vertices[0]]
    for i in range(1, len(vertices) - 1):
        a, b, c = vertices[i - 1], vertices[i], vertices[i + 1]
        r = radii[i - 1]
        u1 = _unit(b[0] - a[0], b[1] - a[1])
        u2 = _unit(c[0] - b[0], c[1] - b[1])
        turn = wrap(math.atan2(u2[1], u2[0]) - math.atan2(u1[1], u1[0]))
        if abs(turn) < 1e-6 or r <= 0:
            out.append(b)
            continue
        t = tans[i]
        # a leg is shared with the neighbouring fillet only as far as that fillet actually needs it
        leg_in = math.dist(a, b) - tans[i - 1]
        leg_out = math.dist(b, c) - tans[i + 1]
        if t > leg_in + 1e-6 or t > leg_out + 1e-6:
            raise ValueError(
                f"radius {r} m at vertex {i} needs {t:.2f} m tangent length but only "
                f"{leg_in:.2f} m and {leg_out:.2f} m of the legs are free; reduce the radius or move the vertex"
            )
        t1 = (b[0] - u1[0] * t, b[1] - u1[1] * t)
        t2 = (b[0] + u2[0] * t, b[1] + u2[1] * t)
        left = turn > 0
        nx, ny = (-u1[1], u1[0]) if left else (u1[1], -u1[0])
        cx, cy = t1[0] + nx * r, t1[1] + ny * r
        a0 = math.atan2(t1[1] - cy, t1[0] - cx)
        a1 = math.atan2(t2[1] - cy, t2[0] - cx)
        out.append(t1)
        out.extend(arc_points(cx, cy, r, a0, a1, ccw=left, step=step)[1:])
    out.append(vertices[-1])
    pts = _dedupe(out)
    if build_up > 0:
        pts = smooth_curvature(pts, build_up)
    return pts


def _dedupe(pts: list[Pt], tol: float = 1e-6) -> list[Pt]:
    res = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, res[-1]) > tol:
            res.append(p)
    return res


def resample(pts: list[Pt], step: float = 0.1) -> list[PathSample]:
    """Resample a polyline at a fixed step and estimate heading and curvature."""
    pts = _dedupe(pts)
    if len(pts) < 2:
        raise ValueError("path has fewer than two distinct points")
    line = LineString(pts)
    total = line.length
    n = max(2, int(total / step) + 1)
    dense = [line.interpolate(total * i / (n - 1)) for i in range(n)]
    xy = [(p.x, p.y) for p in dense]
    heads = []
    for i in range(n):
        j0, j1 = max(i - 1, 0), min(i + 1, n - 1)
        heads.append(math.atan2(xy[j1][1] - xy[j0][1], xy[j1][0] - xy[j0][0]))
    ds = total / (n - 1)
    # curvature from heading change over a +/-k window (smooths polyline facets)
    k = max(1, int(0.5 / ds))
    samples = []
    for i in range(n):
        j0, j1 = max(i - k, 0), min(i + k, n - 1)
        dist = (j1 - j0) * ds
        kap = wrap(heads[j1] - heads[j0]) / dist if dist > 0 else 0.0
        samples.append(PathSample(xy[i][0], xy[i][1], heads[i], kap, i * ds))
    return samples


def smooth_curvature(pts: list[Pt], length: float, step: float = 0.1) -> list[Pt]:
    """Rebuild the path from a moving-average of its curvature (a cheap transition curve)."""
    s = resample(pts, step)
    w = max(1, int(length / step / 2))
    kap = [x.curvature for x in s]
    sm = []
    for i in range(len(kap)):
        a, b = max(0, i - w), min(len(kap), i + w + 1)
        sm.append(sum(kap[a:b]) / (b - a))
    x, y, h = s[0].x, s[0].y, s[0].heading
    out = [(x, y)]
    ds = s[1].s - s[0].s
    for i in range(1, len(s)):
        h += sm[i - 1] * ds
        x += math.cos(h) * ds
        y += math.sin(h) * ds
        out.append((x, y))
    return out


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------
@dataclass
class Pose:
    x: float
    y: float
    theta: float
    steer_deg: float
    s: float
    tx: float | None = None  # trailer axle
    ty: float | None = None
    ttheta: float | None = None
    artic_deg: float | None = None


@dataclass
class RunResult:
    vehicle: str
    direction: str
    reference: str
    poses: list[Pose]
    warnings: list[str] = field(default_factory=list)
    max_steer_used_deg: float = 0.0
    max_artic_deg: float = 0.0

    def ok(self) -> bool:
        return not self.warnings


def simulate(v: Vehicle, path: list[Pt], direction: str = "forward", reference: str = "rear_axle", step: float = 0.1) -> RunResult:
    if direction not in ("forward", "reverse"):
        raise ValueError("direction must be 'forward' or 'reverse'")
    if reference == "auto":
        # Vehicle Tracking Follow steers the FRONT axle along the guide line when driving forwards (seen live:
        # a rear-axle check passed at 0.45 m where Follow's envelope touched the planter). Reverse legs are
        # defined by the rear axle.
        reference = "front_axle" if direction == "forward" else "rear_axle"
    if reference not in ("rear_axle", "front_axle"):
        raise ValueError("reference must be 'rear_axle' or 'front_axle'")
    if reference == "front_axle" and direction == "reverse":
        raise ValueError("front_axle reference is only defined for forward runs; give the rear axle path for reverse legs")

    s = resample(path, step)
    L = v.wheelbase
    poses: list[Pose] = []
    warnings: list[str] = []

    if reference == "rear_axle":
        flip = math.pi if direction == "reverse" else 0.0
        for p in s:
            steer = math.degrees(math.atan(L * p.curvature))
            if direction == "reverse":
                steer = -steer  # body faces opposite to travel; left/right swap
            poses.append(Pose(p.x, p.y, wrap(p.heading + flip), steer, p.s))
    else:  # front axle path, rear follows as tractrix
        fx, fy = s[0].x, s[0].y
        rx, ry = fx - L * math.cos(s[0].heading), fy - L * math.sin(s[0].heading)
        for p in s:
            ux, uy = _unit(p.x - rx, p.y - ry)
            rx, ry = p.x - L * ux, p.y - L * uy
            theta = math.atan2(uy, ux)
            steer = math.degrees(wrap(p.heading - theta))
            poses.append(Pose(rx, ry, theta, steer, p.s))

    if v.articulated:
        kp = v.kingpin_offset
        Lt = v.trailer_wheelbase
        p0 = poses[0]
        hx, hy = p0.x + kp * math.cos(p0.theta), p0.y + kp * math.sin(p0.theta)
        tx, ty = hx - Lt * math.cos(p0.theta), hy - Lt * math.sin(p0.theta)
        for p in poses:
            hx, hy = p.x + kp * math.cos(p.theta), p.y + kp * math.sin(p.theta)
            ux, uy = _unit(hx - tx, hy - ty)
            tx, ty = hx - Lt * ux, hy - Lt * uy
            p.tx, p.ty, p.ttheta = tx, ty, math.atan2(uy, ux)
            p.artic_deg = math.degrees(wrap(p.theta - p.ttheta))
        if direction == "reverse":
            warnings.append(
                "Reversing an articulated vehicle is unstable; this run shows the trailer response "
                "to the given tractor path, not a drivable manoeuvre. Check it in Vehicle Tracking."
            )

    max_steer = max(abs(p.steer_deg) for p in poses)
    if max_steer > v.max_steer_deg + 0.5:
        worst = max(poses, key=lambda p: abs(p.steer_deg))
        warnings.append(
            f"Steering exceeds full lock: {max_steer:.1f} deg needed vs {v.max_steer_deg:.1f} deg available, "
            f"worst at s={worst.s:.1f} m ({worst.x:.2f}, {worst.y:.2f}). Ease the radius there."
        )
    max_artic = max((abs(p.artic_deg) for p in poses if p.artic_deg is not None), default=0.0)
    if v.articulated and max_artic > v.max_articulation_deg:
        warnings.append(f"Articulation reaches {max_artic:.1f} deg, above the {v.max_articulation_deg:.0f} deg limit (jackknife).")

    return RunResult(v.name, direction, reference, poses, warnings, round(max_steer, 1), round(max_artic, 1))


# ---------------------------------------------------------------------------
# Bodies, envelopes, wheel paths
# ---------------------------------------------------------------------------
def _rect(x: float, y: float, th: float, back: float, front: float, half_w: float) -> Polygon:
    c, s = math.cos(th), math.sin(th)
    local = [(-back, -half_w), (front, -half_w), (front, half_w), (-back, half_w)]
    return Polygon([(x + c * px - s * py, y + s * px + c * py) for px, py in local])


def bodies(v: Vehicle, p: Pose) -> list[Polygon]:
    """Body outlines at one pose (tractor first, then trailer)."""
    out = [_rect(p.x, p.y, p.theta, v.rear_overhang, v.wheelbase + v.front_overhang, v.width / 2)]
    if v.articulated and p.tx is not None:
        tw = (v.trailer_width or v.width) / 2
        out.append(_rect(p.tx, p.ty, p.ttheta, v.trailer_rear_overhang, v.trailer_wheelbase + v.trailer_front_overhang, tw))
    return out


def envelope(v: Vehicle, run: RunResult, every: float = 0.25) -> Polygon:
    """Swept envelope: union of body outlines sampled along the run."""
    polys = []
    last_s = -1e9
    for p in run.poses:
        if p.s - last_s >= every or p is run.poses[-1]:
            polys.extend(bodies(v, p))
            last_s = p.s
    return unary_union(polys).buffer(0)


def wheel_paths(v: Vehicle, run: RunResult) -> dict[str, list[Pt]]:
    """Tracks of the outer wheels (front left/right, rear left/right, trailer rear)."""
    ht = v.wheel_track / 2
    paths: dict[str, list[Pt]] = {k: [] for k in ("front_left", "front_right", "rear_left", "rear_right")}
    if v.articulated:
        paths["trailer_left"], paths["trailer_right"] = [], []
    for p in run.poses:
        c, s = math.cos(p.theta), math.sin(p.theta)
        fx, fy = p.x + c * v.wheelbase, p.y + s * v.wheelbase
        paths["front_left"].append((fx - s * ht, fy + c * ht))
        paths["front_right"].append((fx + s * ht, fy - c * ht))
        paths["rear_left"].append((p.x - s * ht, p.y + c * ht))
        paths["rear_right"].append((p.x + s * ht, p.y - c * ht))
        if v.articulated and p.tx is not None:
            tc, ts = math.cos(p.ttheta), math.sin(p.ttheta)
            paths["trailer_left"].append((p.tx - ts * ht, p.ty + tc * ht))
            paths["trailer_right"].append((p.tx + ts * ht, p.ty - tc * ht))
    return paths


def outlines_at(v: Vehicle, run: RunResult, interval: float) -> list[list[Pt]]:
    """Vehicle outlines every `interval` metres plus the start and end positions."""
    out, last = [], -1e9
    for p in run.poses:
        if p.s - last >= interval or p is run.poses[-1]:
            for b in bodies(v, p):
                out.append(list(b.exterior.coords))
            last = p.s
    return out


# ---------------------------------------------------------------------------
# Clearance
# ---------------------------------------------------------------------------
@dataclass
class Obstacle:
    geom: LineString | Polygon
    label: str  # e.g. layer name and handle


def clearance(v: Vehicle, run: RunResult, obstacles: list[Obstacle], required: float = 0.3, every: float = 0.2) -> dict:
    """Minimum distance from the vehicle body to each obstacle along the run.

    Returns the worst clearance overall, every obstacle closer than `required`,
    and any outright conflicts (overlap).
    """
    if not obstacles:
        return {"min_clearance_m": None, "required_m": required, "pass": True, "conflicts": [], "tight": [], "note": "no obstacles given"}
    geoms = [o.geom for o in obstacles]
    tree = STRtree(geoms)
    worst: dict[int, tuple[float, Pose, Pt]] = {}
    last = -1e9
    search = required + 2.0
    for p in run.poses:
        if p.s - last < every and p is not run.poses[-1]:
            continue
        last = p.s
        for b in bodies(v, p):
            for idx in tree.query(b.buffer(search)):
                g = geoms[int(idx)]
                d = b.distance(g)
                if b.intersects(g):
                    d = -b.intersection(g).length if g.geom_type == "LineString" else -math.sqrt(b.intersection(g).area)
                if int(idx) not in worst or d < worst[int(idx)][0]:
                    np_ = nearest_points(b, g)[1]
                    worst[int(idx)] = (d, p, (np_.x, np_.y))
    if not worst:
        return {"min_clearance_m": f"> {search:.1f}", "required_m": required, "pass": True, "conflicts": [], "tight": []}
    rows = []
    for idx, (d, p, near) in sorted(worst.items(), key=lambda kv: kv[1][0]):
        rows.append(
            {
                "obstacle": obstacles[idx].label,
                "clearance_m": round(d, 3),
                "at_s_m": round(p.s, 1),
                "vehicle_rear_axle": [round(p.x, 3), round(p.y, 3)],
                "nearest_obstacle_point": [round(near[0], 3), round(near[1], 3)],
            }
        )
    conflicts = [r for r in rows if r["clearance_m"] < 0]
    tight = [r for r in rows if 0 <= r["clearance_m"] < required]
    return {
        "min_clearance_m": rows[0]["clearance_m"],
        "required_m": required,
        "pass": not conflicts and not tight,
        "conflicts": conflicts,
        "tight": tight,
        "nearest": rows[:5],
        "note": "Negative clearance = overlap (value is overlap length for lines, approx depth for areas).",
    }


# ---------------------------------------------------------------------------
# Junction geometry
# ---------------------------------------------------------------------------
def line_intersection(p1: Pt, d1: Pt, p2: Pt, d2: Pt) -> Pt:
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        raise ValueError("lines are parallel")
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return p1[0] + d1[0] * t, p1[1] + d1[1] * t


def kerb_return(edge_a: tuple[Pt, Pt], edge_b: tuple[Pt, Pt], radius: float, three_centred: tuple[float, float, float] | None = None, step: float = 0.25) -> dict:
    """Kerb return joining two kerb lines that meet at a corner.

    edge_a, edge_b: each a (point_far_from_corner, point_towards_corner) pair, so
        the direction of travel runs along edge_a into the corner and out along edge_b.
    radius: simple circular radius, used when three_centred is None.
    three_centred: (R1, R2, R3) compound curve radii with R1 = R3 > R2 typical
        (e.g. (36, 12, 36) for a large truck corner). Splits the deflection 1:3:1... approximately.
    """
    a0, a1 = edge_a
    b1, b0 = edge_b  # b1 near the corner, b0 far
    da = _unit(a1[0] - a0[0], a1[1] - a0[1])
    db = _unit(b0[0] - b1[0], b0[1] - b1[1])
    corner = line_intersection(a0, da, b1, db)
    if three_centred is None:
        pts = fillet_path([a0, corner, b0], [radius], step=step)
        info = {"type": "simple", "radius": radius}
    else:
        r1, r2, r3 = three_centred
        turn = wrap(math.atan2(db[1], db[0]) - math.atan2(da[1], da[0]))
        sgn = 1 if turn > 0 else -1
        # deflection split: outer arcs 20 % each, centre arc 60 % (a common AASHTO-style proportion)
        defl = [0.2 * abs(turn), 0.6 * abs(turn), 0.2 * abs(turn)]
        # build by marching from a provisional start, then translate so the end lies on edge_b
        h = math.atan2(da[1], da[0])
        x, y = 0.0, 0.0
        seq = [(0.0, 0.0)]
        for r, d in zip((r1, r2, r3), defl):
            cx, cy = x - sgn * r * math.sin(h), y + sgn * r * math.cos(h)
            a_start = math.atan2(y - cy, x - cx)
            a_end = a_start + sgn * d
            arc = arc_points(cx, cy, r, a_start, a_end, ccw=sgn > 0, step=step)
            seq.extend(arc[1:])
            x, y = arc[-1]
            h += sgn * d
        # The curve starts tangent to edge_a and ends tangent to edge_b. Find the start
        # point S on edge_a such that S + (curve end offset) lands on edge_b.
        end = seq[-1]
        start = line_intersection(a0, da, (b1[0] - end[0], b1[1] - end[1]), db)
        curve = [(start[0] + px, start[1] + py) for px, py in seq]
        pts = [a0] + curve + [b0]
        info = {"type": "three_centred", "radii": [r1, r2, r3], "deflection_split": "20/60/20 %"}
    info["corner"] = [round(corner[0], 3), round(corner[1], 3)]
    info["points"] = pts
    return info


def taper(start: Pt, direction_deg: float, length: float, offset: float) -> list[Pt]:
    """Straight taper for a flare or lane gain: offset (+ left) over length."""
    h = math.radians(direction_deg)
    c, s = math.cos(h), math.sin(h)
    return [start, (start[0] + c * length - s * offset, start[1] + s * length + c * offset)]


def turn_guide(entry_point: Pt, entry_heading_deg: float, exit_heading_deg: float, radius: float, lead_in: float = 15.0, lead_out: float = 15.0) -> list[Pt]:
    """A simple turning guide line: straight lead-in, circular turn, straight lead-out.

    entry_point is where the lead-in starts (in the approach lane centre).
    """
    h1, h2 = math.radians(entry_heading_deg), math.radians(exit_heading_deg)
    turn = wrap(h2 - h1)
    t = radius * math.tan(abs(turn) / 2)
    v1 = (entry_point[0] + math.cos(h1) * (lead_in + t), entry_point[1] + math.sin(h1) * (lead_in + t))
    end = (v1[0] + math.cos(h2) * (t + lead_out), v1[1] + math.sin(h2) * (t + lead_out))
    return fillet_path([entry_point, v1, end], [radius])


# ---------------------------------------------------------------------------
# Sight distance (TIA access checks)
# ---------------------------------------------------------------------------
def stopping_sight_distance(speed_kmh: float, grade_pct: float = 0.0, reaction_s: float = 2.5, decel: float = 3.4) -> float:
    """AASHTO 2018 SSD: 0.278 V t + V^2 / (254 (a/9.81 + G)). Check the value against MoWT RDM tables."""
    g = grade_pct / 100.0
    return 0.278 * speed_kmh * reaction_s + speed_kmh**2 / (254 * (decel / 9.81 + g))


def visibility_splay(access_point: Pt, road_heading_deg: float, x_dist: float, y_left: float, y_right: float, left_hand_traffic: bool = True) -> dict:
    """Junction visibility splay at an access.

    access_point: point on the near kerb line at the centre of the access.
    road_heading_deg: direction of the main road (any of the two senses).
    x_dist: set-back along the access centreline (e.g. 2.4 m, 4.5 m).
    y_left / y_right: distances along the near-side edge as seen by the emerging driver.
    """
    h = math.radians(road_heading_deg)
    ux, uy = math.cos(h), math.sin(h)
    nx, ny = -uy, ux  # left normal of road direction
    # driver eye is set back from the kerb, away from the road: decide side by convention
    eye = (access_point[0] - nx * x_dist, access_point[1] - ny * x_dist)
    pl = (access_point[0] - ux * y_left, access_point[1] - uy * y_left)
    pr = (access_point[0] + ux * y_right, access_point[1] + uy * y_right)
    return {
        "eye_point": eye,
        "splay_left": [eye, pl],
        "splay_right": [eye, pr],
        "polygon": [pl, eye, pr],
        "note": "Eye point is placed on the right-hand normal of road_heading; pass road_heading +180 deg if it lands on the wrong side.",
    }


# =============================================================================
# turn-round search (three-point and T-turns), a kinematic check
# =============================================================================
def _arc_poses(x: float, y: float, th: float, radius: float, sweep: float, reverse: bool, step: float = 0.1) -> list[tuple[float, float, float]]:
    """Rear-axle poses along an arc. radius > 0 turns left, < 0 right; 0 = straight of length `sweep`."""
    out = []
    if radius == 0:
        n = max(1, int(sweep / step))
        d = (-1 if reverse else 1) * sweep / n
        for _ in range(n):
            x += d * math.cos(th)
            y += d * math.sin(th)
            out.append((x, y, th))
        return out
    length = abs(radius * sweep)
    n = max(1, int(length / step))
    d = (-1 if reverse else 1) * length / n
    for _ in range(n):
        th += d / radius
        x += d * math.cos(th)
        y += d * math.sin(th)
        out.append((x, y, th))
    return out


def turn_search(
    v: Vehicle,
    start: tuple[float, float, float],
    obstacles: list[Obstacle],
    required: float = 0.3,
    target_heading: float | None = None,
    heading_tol_deg: float = 15.0,
    lateral_band: tuple[float, float] | None = None,
    max_forward_m: float = 25.0,
) -> dict:
    """Search for a three-point (or T) turn that brings a rigid vehicle round to `target_heading`.

    start = (x, y, heading_rad) of the REAR AXLE where the manoeuvre may begin (e.g. driving into a yard).
    Stage 1 drives forward (a straight then an arc), stage 2 reverses on an arc, stage 3 drives forward on an
    arc until the heading is within heading_tol of the target (default: start heading + 180 deg).
    lateral_band = (min, max) of the final position measured across the start heading (e.g. the aisle),
    optional. Returns the best sequence (largest minimum clearance) or the closest miss. This is a
    kinematic check, not a Vehicle Tracking run, and must be labelled as such.
    """
    if v.articulated:
        raise ValueError("turn_search covers rigid vehicles only")
    target = wrap(start[2] + math.pi) if target_heading is None else target_heading
    rmin = v.min_rear_axle_radius
    radii = [rmin * f for f in (1.02, 1.12, 1.25)]
    geoms = [o.geom for o in obstacles]
    union = None
    try:
        from shapely.ops import unary_union

        union = unary_union(geoms) if geoms else None
    except Exception:
        union = None

    def clr(poses: list[tuple[float, float, float]]) -> float:
        if union is None:
            return 99.0
        worst = 99.0
        for x, y, th in poses[::3]:
            b = _rect(x, y, th, v.rear_overhang, v.wheelbase + v.front_overhang, v.width / 2)
            if b.intersects(union):
                return -1.0
            worst = min(worst, b.distance(union))
        return worst

    sx, sy, sth = start
    best: dict | None = None
    near: dict | None = None
    tried = 0
    for lead in [x * 0.5 for x in range(0, int(max_forward_m / 0.5) + 1, 2)]:
        base = _arc_poses(sx, sy, sth, 0, lead, False) if lead else []
        c0 = clr(base) if base else 99.0
        if c0 < 0:
            break  # driving further forward hits something
        x0, y0, t0 = base[-1] if base else start
        for s1 in (1, -1):
            for r1 in radii:
                for a1 in (15, 30, 45, 60, 75, 90):
                    p1 = _arc_poses(x0, y0, t0, s1 * r1, math.radians(a1), False)
                    c1 = clr(p1)
                    if c1 < 0:
                        continue
                    for s2 in (1, -1):
                        for r2 in radii:
                            for a2 in (30, 45, 60, 75, 90, 105, 120):
                                p2 = _arc_poses(*p1[-1], s2 * r2, math.radians(a2), True)
                                c2 = clr(p2)
                                if c2 < 0:
                                    continue
                                x2, y2, t2 = p2[-1]
                                for s3 in (1, -1):
                                    for r3 in radii:
                                        dt = wrap(target - t2)
                                        sweep = dt if s3 > 0 else -dt
                                        if sweep < 0:
                                            sweep += 2 * math.pi
                                        if sweep > math.radians(150):
                                            continue
                                        tried += 1
                                        p3 = _arc_poses(x2, y2, t2, s3 * r3, sweep, False)
                                        xe, ye, te = p3[-1]
                                        if lateral_band is not None:
                                            lat = -(xe - sx) * math.sin(sth) + (ye - sy) * math.cos(sth)
                                            if not (lateral_band[0] <= lat <= lateral_band[1]):
                                                continue
                                        c3 = clr(p3)
                                        m = min(c0, c1, c2, c3)
                                        cand = {
                                            "min_clearance_m": round(m, 3),
                                            "stages": [
                                                {"stage": "forward", "lead_in_m": lead, "radius_m": round(s1 * r1, 2), "turn_deg": a1,
                                                 "rear_axle_path": [(round(p[0], 3), round(p[1], 3)) for p in (base + p1)[::5]]},
                                                {"stage": "reverse", "radius_m": round(s2 * r2, 2), "turn_deg": a2,
                                                 "rear_axle_path": [(round(p[0], 3), round(p[1], 3)) for p in p2[::5]]},
                                                {"stage": "forward", "radius_m": round(s3 * r3, 2), "turn_deg": round(math.degrees(sweep), 1),
                                                 "rear_axle_path": [(round(p[0], 3), round(p[1], 3)) for p in p3[::5]]},
                                            ],
                                        }
                                        if m >= 0 and (best is None or m > best["min_clearance_m"]):
                                            best = cand
                                        if m < 0 and (near is None or m > near["min_clearance_m"]):
                                            near = cand
    if best and best["min_clearance_m"] >= required:
        verdict = f"three-point turn found with {best['min_clearance_m']:.2f} m minimum clearance"
    elif best:
        verdict = f"a three-point turn only fits with {best['min_clearance_m']:.2f} m clearance, below the {required:.2f} m target"
    else:
        verdict = "no three-point turn fits; the vehicle cannot turn round here in three moves"
    return {
        "vehicle": v.name,
        "verdict": verdict,
        "pass": bool(best and best["min_clearance_m"] >= required),
        "best": best,
        "sequences_tried": tried,
        "label": "Kinematic check (built-in engine), not a Vehicle Tracking run.",
    }
