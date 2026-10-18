"""v0.3 fixes from the Hoima job (18 Oct 2026)."""

import os
import tempfile

os.environ.setdefault("CIVIL3D_MCP_HOME", tempfile.mkdtemp(prefix="c3dmcp-test-"))

import math  # noqa: E402

import pytest  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402

from civil3d_mcp import catalogue as catmod  # noqa: E402
from civil3d_mcp import geometry as geo  # noqa: E402
from civil3d_mcp import vt, winput  # noqa: E402
from civil3d_mcp.com_bridge import Civil3D  # noqa: E402
from civil3d_mcp.vehicles import get_vehicle  # noqa: E402
from tests.test_dialogs_flow import D1_P, D1_SU  # noqa: E402


def test_default_vehicle_answers_no_when_another_vehicle_is_wanted(tmp_path):
    cat = catmod.Catalogue(tmp_path / "c.json")
    cat.learn(D1_P, "vt.default_vehicle")
    ans = vt.build_answers(vehicle="SU - Single Unit", vehicle_path=["South African Design Vehicles", "SANRAL", "SU - Single Unit"])
    acts, why = catmod.resolve_answer(cat.match(D1_P), ans)
    assert acts == [{"click": "No"}] and "else-answer" in why
    assert catmod.resolve_answer(cat.match(D1_SU), ans)[0] == [{"click": "Yes"}]


def test_command_line_window_is_not_a_dialog():
    w = {"controls": [{"type": "List"}, {"type": "ListItem", "name": winput.CMDLINE_MARK}, {"type": "Text", "name": "Command: x"}]}
    assert winput.is_command_line(w)
    assert not winput.is_command_line(D1_P["windows"][0])


def test_prompt_detection():
    assert winput.looks_like_prompt(["Command: DRIVEFOLLOW", "Follow Drive: <select object to Follow>"])
    assert winput.looks_like_prompt(["Command:"]) is None


def test_empty_window_input_refused():
    with pytest.raises((ValueError, RuntimeError)):
        winput.send_text(1, "  ")


def test_fillet_radius_zero_vertex_gives_whole_leg():
    # failed in v0.2: a straight-through middle vertex halved the next fillet's leg
    pts = geo.fillet_path([(17.5, 4.4), (5.8, 4.4), (-3.8, 4.4), (-3.8, -2.0)], [0, 5.8])
    assert pts[0] == (17.5, 4.4) and pts[-1] == (-3.8, -2.0)


def test_reference_auto_is_front_axle_forwards():
    v = get_vehicle("P")
    path = geo.fillet_path([(0, 0), (20, 0), (20, 20)], [8])
    assert geo.simulate(v, path, "forward", "auto").reference == "front_axle"
    assert geo.simulate(v, path, "reverse", "auto").reference == "rear_axle"


def test_turn_search_hoima_yard_has_no_three_point_turn():
    v = get_vehicle("P")
    bld = Polygon([(0.84, 14.42), (29.57, 14.42), (29.57, 1.09), (22.15, 0.89), (22.15, 6.74), (23.94, 6.74), (23.94, 8.8), (3.07, 8.8), (3.07, 6.87), (0.84, 6.87)])
    obs = [geo.Obstacle(bld, "b"), geo.Obstacle(box(-1.42, -0.4, 30.3, 0), "wall"), geo.Obstacle(box(0.72, 0.18, 4.12, 2.34), "waste")]
    cars = [box(4.315, 6.1, 9.115, 7.9), box(9.875, 6.1, 14.675, 7.9), box(15.135, 6.1, 19.935, 7.9), box(7.2, 0.73, 12.0, 2.53), box(12.985, 0.73, 17.785, 2.53)]
    obs += [geo.Obstacle(c, "car") for c in cars]
    r = geo.turn_search(v, (5.0, 4.4, 0.0), obs, 0.3, lateral_band=(-1.5, 2.5), max_forward_m=14)
    assert not r["pass"] and r["best"] is None


def test_turn_search_open_road_finds_a_turn():
    v = get_vehicle("P")
    obs = [geo.Obstacle(box(-5, -0.4, 60, 0), "s"), geo.Obstacle(box(-5, 10.5, 60, 10.9), "n")]
    r = geo.turn_search(v, (5.0, 2.0, 0.0), obs, 0.3)
    assert r["pass"]


def test_working_copy_names():
    assert Civil3D.is_working_copy(r"G:\\Jobs\\Hoima\\Hoima_swept_path_base_claude.dxf.dwg")
    assert Civil3D.is_working_copy(r"G:\\Jobs\\Hoima\\x_claude.dwg")
    assert not Civil3D.is_working_copy(r"G:\\Jobs\\Hoima\\Architect.dwg")
