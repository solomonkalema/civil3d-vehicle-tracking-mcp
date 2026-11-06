"""v0.6: launching a fresh Civil 3D session (26 Oct 2026)."""
from civil3d_mcp.launcher import Launcher, rank_shortcut


def test_rank_prefers_metric_2027():
    assert rank_shortcut("Civil 3D 2027 Metric.lnk") == rank_shortcut("Civil 3D 2027 - Metric.lnk")
    names = ["Civil 3D 2027 - Imperial.lnk", "Civil 3D 2027 - Metric.lnk", "Civil 3D 2027.lnk",
             "Civil 3D 2026 - Metric.lnk"]
    best = max(names, key=lambda n: rank_shortcut(n) or -1)
    assert best == "Civil 3D 2027 - Metric.lnk"
    assert rank_shortcut("Civil 3D 2027.lnk") > rank_shortcut("Civil 3D 2027 - Imperial.lnk")
    assert rank_shortcut("Civil 3D 2026 - Metric.lnk") < rank_shortcut("Civil 3D 2027 - Imperial.lnk")


def test_rank_skips_utilities():
    for n in ("Reset Settings to Default - Civil 3D 2027.lnk", "Migrate Custom Settings - Civil 3D 2027.lnk",
              "Civil 3D 2027 Object Enabler.lnk", "Subassembly Composer for Civil 3D 2027.lnk",
              "Civil 3D 2027 - Metric.url", "AutoCAD 2027.lnk", "Civil 3D - Metric.lnk"):
        assert rank_shortcut(n) is None, n


def test_report_before_start():
    rep = Launcher(c3d=None).report()
    assert rep == {"launch_state": "idle"}
