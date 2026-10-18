from civil3d_mcp import section as sec

P = sec.SideProfile("P", 3.1, 0.7, 1.0, 0.12, 0.12, 1.7)


def test_flat_floor_clearance_equals_ground_clearance():
    r = sec.ramp_check(P, [(0, 0), (20, 0)], [(0, 2.4), (20, 2.4)])
    assert abs(r["underbody"]["min"]["clearance_m"] - 0.12) < 1e-3
    assert abs(r["headroom"]["min"]["clearance_m"] - 0.70) < 1e-3
    assert r["passes"]


def test_sharp_crest_grounds_between_axles():
    # flat, then 20% down with no transition: breakover at the crest
    r = sec.ramp_check(P, [(0, 0), (10, 0), (25, -3)])
    assert not r["underbody"]["passes"]
    assert any("breakover" in f["part"] for f in r["underbody"]["failures"])


def test_transition_fixes_crest():
    # same 20% ramp with transitions in 5% steps every 2 m at each end
    floor = [(0, 0), (10, 0), (12, -0.1), (14, -0.3), (16, -0.6), (22, -1.8), (24, -2.1), (26, -2.3), (28, -2.4), (38, -2.4)]
    r = sec.ramp_check(P, floor)
    assert r["underbody"]["passes"], r["underbody"]
    assert r["ramp"]["max_grade_pct"] == 20.0


def test_sag_hits_overhang_both_directions_reported():
    floor = [(0, 3), (12, 0), (30, 0)]  # 25% down into a flat floor, no transition
    r = sec.ramp_check(P, floor)
    parts = {f["part"] for f in r["underbody"]["failures"]}
    assert any("approach" in p or "departure" in p for p in parts)


def test_low_beam_fails_headroom():
    r = sec.ramp_check(P, [(0, 0), (20, 0)], [(0, 2.4), (9, 2.4), (9.01, 1.75), (11, 1.75), (11.01, 2.4), (20, 2.4)])
    assert not r["headroom"]["passes"]
    assert r["ramp"]["min_static_headroom_m"] < 1.8


def test_png(tmp_path):
    floor = [(0, 0), (10, 0), (25, -3), (35, -3)]
    soffit = [(0, 2.4), (35, 2.4)]
    r = sec.ramp_check(P, floor, soffit)
    out = sec.render_png(str(tmp_path / "s.png"), P, floor, soffit, r)
    assert (tmp_path / "s.png").stat().st_size > 1000
