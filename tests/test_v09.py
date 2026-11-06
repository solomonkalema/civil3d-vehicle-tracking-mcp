"""v0.9: road production helpers (road pilot, 27 Oct 2026)."""
import pytest

from civil3d_mcp import com_bridge, dialogs, production as prod
from civil3d_mcp.com_bridge import Civil3DError, refuse_shared
from civil3d_mcp.watchdog import classify


def _w(title, buttons=(), inputs=(), cls="#32770"):
    ctrls = [{"type": "Button", "name": b} for b in buttons] + [{"type": t, "name": n} for t, n in inputs]
    return {"title": title, "class": cls, "text": [], "controls": ctrls}


# ---------------------------------------------------------------- progress windows
def test_plot_progress_is_left_alone():
    w = _w("Plot Progress", ["Cancel Sheet", "Cancel Entire Job"])
    assert classify(w) == ("progress", None)  # before v0.9 the watchdog pressed Cancel and aborted the plot
    assert dialogs.is_transient({"windows": [w]})


def test_plot_job_progress_without_controls_is_transient():
    # seen live 27 Oct 2026 during -PLOT: no readable controls, title 'Plot Job Progress'
    assert dialogs.is_transient({"windows": [{"title": "Plot Job Progress", "controls": []}]})


def test_progress_with_inputs_is_not_transient():
    w = _w("Plot Progress", ["OK", "Cancel"], [("Edit", "File name")])
    assert not dialogs.is_progress(w)


def test_ordinary_cancelable_unchanged():
    assert classify(_w("Select Template", ["Open", "Cancel"])) == ("cancelable", "Cancel")


def test_object_name_change_is_protected():
    w = _w("Object Name Change", ["Update the reference name", "Keep the current name"])
    assert classify(w)[0] == "protected"


# ---------------------------------------------------------------- quantities
def _square(x0, y0, size):
    pts = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    return list(zip(pts, pts[1:] + pts[:1]))


def test_loops_and_area():
    segs = _square(0, 0, 2) + _square(10, 10, 1)
    rings = prod.loops(list(reversed(segs)))
    assert sorted(round(prod.ring_area(r), 6) for r in rings) == [1.0, 4.0]


def test_average_end_area():
    rows = [{"station": 0, "cut": 2.0}, {"station": 20, "cut": 4.0, "AC": 1.0}, {"station": 40, "cut": 0.0}]
    v = prod.average_end_area(rows)
    assert v["cut"] == 20 * 3 + 20 * 2
    assert v["AC"] == 20 * 0.5 + 20 * 0.5


def test_pick_ground_skips_corridor_sections():
    names = ["Road A Corridor", "Road A Corridor - Top", "Road A & B Topo"]
    assert prod.pick_ground(names) == 2
    assert prod.pick_ground(["Datum", "Existing Ground"]) == 1
    assert prod.pick_ground(names, "corridor - top") == 1
    assert prod.pick_ground(["Road A Corridor"]) is None


def test_stations_end_exactly():
    st = prod.stations_every(20.0, 644.613, 10)
    assert st[0] == 20.0 and st[-1] == 644.613 and st[-2] == 640.0


# ---------------------------------------------------------------- plotting
def test_plot_target_never_overwrites(tmp_path):
    (tmp_path / "PP-01.pdf").write_bytes(b"%PDF")
    (tmp_path / "PP-01 v2.pdf").write_bytes(b"%PDF")
    p, note = prod.plot_target(tmp_path, "PP-01", overwrite=False)
    assert p.name == "PP-01 v3.pdf" and "already exists" in note
    p, note = prod.plot_target(tmp_path, "CS-01.pdf", overwrite=False)
    assert p.name == "CS-01.pdf" and note is None


def test_plot_target_overwrite_when_free(tmp_path):
    (tmp_path / "PP-01.pdf").write_bytes(b"%PDF")
    p, note = prod.plot_target(tmp_path, "PP-01", overwrite=True)
    assert p.name == "PP-01.pdf" and note is None and not p.exists()


def test_lisp_string_quoting():
    assert prod._lisp_str('C:\\Jobs\\a "b".pdf') == '"C:/Jobs/a \\"b\\".pdf"'


def test_plot_folder_in_shared_project_refused(monkeypatch, tmp_path):
    shared, staging = tmp_path / "Shared", tmp_path / "Staging"
    monkeypatch.setattr(com_bridge, "_shared_roots", lambda: [(str(shared).lower(), staging)])
    with pytest.raises(Civil3DError):
        refuse_shared(shared / "04 - Drawings" / "x.pdf", "plot into")
    refuse_shared(staging / "04 - Drawings" / "x.pdf", "plot into")  # staging is fine


# ---------------------------------------------------------------- version check
def test_restart_needed_when_disk_is_newer(monkeypatch):
    monkeypatch.setattr(com_bridge, "disk_version", lambda: "9.9.9")
    assert "restart_needed" in com_bridge.version_note()
    monkeypatch.setattr(com_bridge, "disk_version", lambda: com_bridge.SERVER_VERSION)
    assert com_bridge.version_note() == {}


def test_disk_version_reads_pyproject():
    assert com_bridge.disk_version() == com_bridge.SERVER_VERSION
