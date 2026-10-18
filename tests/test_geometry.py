import math

import pytest
from shapely.geometry import LineString, Polygon

from civil3d_mcp import geometry as g
from civil3d_mcp.vehicles import BUILTIN, get_vehicle


def test_vehicle_derived_values():
    p = get_vehicle("P")
    assert math.isclose(p.length, 4.8)
    assert 4.5 < p.min_rear_axle_radius < 5.0
    wb = get_vehicle("WB-15")
    assert math.isclose(wb.length, 17.0, abs_tol=1e-9)


def test_fillet_path_tangent_and_radius():
    pts = g.fillet_path([(0, 0), (20, 0), (20, 20)], [8.0], step=0.05)
    s = g.resample(pts, 0.05)
    kmax = max(abs(x.curvature) for x in s)
    assert math.isclose(kmax, 1 / 8.0, rel_tol=0.08)
    assert math.isclose(s[0].heading, 0, abs_tol=1e-6)
    assert math.isclose(s[-1].heading, math.pi / 2, abs_tol=1e-3)


def test_fillet_too_big_raises():
    with pytest.raises(ValueError):
        g.fillet_path([(0, 0), (5, 0), (5, 5)], [10.0])


def test_rigid_turn_steer_matches_bicycle_model():
    su = get_vehicle("SU")
    r = 12.0
    pts = g.fillet_path([(0, 0), (40, 0), (40, 40)], [r], step=0.05)
    run = g.simulate(su, pts)
    expected = math.degrees(math.atan(su.wheelbase / r))
    assert math.isclose(run.max_steer_used_deg, expected, abs_tol=1.5)
    assert run.ok()


def test_too_tight_turn_is_flagged():
    su = get_vehicle("SU")
    pts = g.fillet_path([(0, 0), (30, 0), (30, 30)], [4.0], step=0.05)
    run = g.simulate(su, pts)
    assert any("full lock" in w for w in run.warnings)


def test_reverse_heading_is_flipped():
    p = get_vehicle("P")
    run = g.simulate(p, [(0, 0), (10, 0)], direction="reverse")
    assert math.isclose(abs(run.poses[0].theta), math.pi, abs_tol=1e-6)


def test_front_axle_reference_offtracks_inside():
    su = get_vehicle("SU")
    r = 15.0
    pts = g.fillet_path([(0, 0), (60, 0), (60, 60)], [r], step=0.05)
    run = g.simulate(su, pts, reference="front_axle")
    # find steady-state rear axle radius around the arc centre (45, 15)
    cx, cy = 60 - r, r
    mid = run.poses[len(run.poses) // 2]
    rr = math.dist((mid.x, mid.y), (cx, cy))
    expected = math.sqrt(r**2 - su.wheelbase**2)
    assert abs(rr - expected) < 0.6


def test_semi_trailer_offtracking_and_envelope():
    wb = get_vehicle("WB-15")
    pts = g.fillet_path([(0, 0), (60, 0), (60, 60)], [15.0], step=0.05)
    run = g.simulate(wb, pts)
    assert run.max_artic_deg > 20
    env = g.envelope(wb, run)
    assert env.area > 0
    # the trailer cuts inside the tractor path, so the envelope is wider than the body
    assert env.area > wb.width * 100


def test_clearance_detects_conflict_and_pass():
    p = get_vehicle("P")
    run = g.simulate(p, [(0, 0), (20, 0)])
    wall_far = g.Obstacle(LineString([(0, 3), (20, 3)]), "far wall")
    wall_hit = g.Obstacle(LineString([(10, -5), (10, 5)]), "cross wall")
    res = g.clearance(p, run, [wall_far])
    assert res["pass"] and math.isclose(res["min_clearance_m"], 3 - 0.9, abs_tol=0.01)
    res = g.clearance(p, run, [wall_far, wall_hit])
    assert not res["pass"] and res["conflicts"][0]["obstacle"] == "cross wall"


def test_bulge_quarter_circle():
    pts = g.bulge_points((10, 0), (0, 10), math.tan(math.pi / 8), step=0.1)
    for x, y in pts:
        assert math.isclose(math.hypot(x, y), 10, abs_tol=1e-6)


def test_kerb_return_simple_and_three_centred():
    simple = g.kerb_return(((-30, 0), (-5, 0)), ((0, 5), (0, 30)), 10)
    assert simple["corner"] == [0.0, 0.0]
    tc = g.kerb_return(((-60, 0), (-5, 0)), ((0, 5), (0, 60)), 0, three_centred=(36, 12, 36))
    pts = tc["points"]
    curve_start, curve_end = pts[1], pts[-2]
    assert abs(curve_start[1]) < 1e-6  # starts on edge_a (y = 0)
    assert abs(curve_end[0]) < 1e-6  # ends on edge_b (x = 0)


def test_ssd_reasonable():
    assert 60 < g.stopping_sight_distance(50) < 70  # AASHTO 50 km/h SSD ~65 m
