import asyncio, os
from civil3d_mcp import server as S


def call(fn, **kw):
    f = getattr(fn, "fn", fn)
    return asyncio.run(f(**kw))


def test_ramp_tool_points(tmp_path):
    r = call(S.sp_ramp_section, vehicle="P", floor_points=[[38, -2.4], [28, -2.4], [10, 0], [0, 0]],
             soffit_points=[[0, 2.4], [38, 2.4]], ground_clearance=0.12, png_path=str(tmp_path / "r.png"))
    assert "error" not in r, r
    assert r["underbody"]["passes"] is False  # 13% grade change at each end, no transitions
    assert os.path.exists(r["png"])
    assert "_worst_poses" not in r


def test_ramp_tool_su_uses_vt_profile():
    r = call(S.sp_ramp_section, vehicle="SU", floor_points=[[0, 0], [30, 0]])
    assert r["side_profile"]["body_height"] == 3.632


def test_cycle_connected_and_parked():
    ingress = {"name": "ingress", "guide_points": [[0, 0], [15, 0]], "direction": "forward"}
    park = {"name": "park", "guide_points": [[15, 0], [20, 0]], "direction": "forward"}
    egress = {"name": "egress", "guide_points": [[20, 0], [35, 0]], "direction": "forward"}
    bay = [[16, -1.25], [24, -1.25], [24, 1.25], [16, 1.25]]
    r = call(S.sp_cycle_check, vehicle="P", stages=[ingress, park, egress], bay_points=bay)
    assert "error" not in r, r
    assert r["stages_connected"], r["stages"]
    assert "parked" in r


def test_cycle_detects_gap():
    a = {"guide_points": [[0, 0], [10, 0]]}
    b = {"guide_points": [[14, 0], [24, 0]]}
    r = call(S.sp_cycle_check, vehicle="P", stages=[a, b])
    assert r["stages_connected"] is False and r["passes"] is False
