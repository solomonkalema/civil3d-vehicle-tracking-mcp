"""v0.5: lessons from a TIA job (20 Oct 2026)."""
import math

from civil3d_mcp import circulation as circ
from civil3d_mcp import winput
from civil3d_mcp.catalogue import compact
from civil3d_mcp.dialogs import is_transient
from civil3d_mcp.lispdata import paren_balance


def test_paren_balance_ok_and_strings():
    assert paren_balance('(setq a "x ( y")') == (0, None)
    assert paren_balance('(princ "a \\" (")') == (0, None)
    assert paren_balance("(+ 1 2) ; (unclosed comment") == (0, None)


def test_paren_balance_catches_truncation():
    depth, problem = paren_balance("(progn (entmake (list (cons 0 \"LINE\")")
    assert depth == 3 and "not closed" in problem
    assert paren_balance("(a))")[1] is not None
    assert "string" in paren_balance('(princ "abc)')[1]


def test_lisp_continuation_prompt():
    assert winput.lisp_continuation(["Command:", "((((((((((_>"])
    assert winput.lisp_continuation(["(_>"])
    assert winput.lisp_continuation(["Command:", "Select object to Follow:"]) is None


def test_transient_splash():
    splash = {"windows": [{"title": "Vehicle Tracking. Please Wait....", "controls": []}]}
    explorer = {"windows": [{"title": "Vehicle Library Explorer", "controls": [{"type": "Button", "name": "Proceed"}]}]}
    assert is_transient(splash)
    assert not is_transient(explorer)


def test_compact_counts_nameless_tree_items():
    ctrls = [{"id": "c1", "type": "Button", "name": "Proceed"}] + [
        {"id": f"c{i}", "type": "TreeItem", "selected": i == 40} for i in range(2, 50)]
    out = compact({"windows": [{"title": "Vehicle Library Explorer", "controls": ctrls}]})
    listed = out["windows"][0]["controls"]
    assert len(listed) == 2  # the button and one note
    assert "48 TreeItems" in listed[1]["note"] and "c40" in listed[1]["note"]


def test_arrows_point_along_travel():
    tris = circ.arrows_along([[0, 0], [20, 0]], spacing=5, size=1.0)
    assert len(tris) == 4
    tip, left, right = tris[0]
    assert tip[0] > left[0] and math.isclose(left[1], -right[1])
    rev = circ.arrows_along([[0, 0], [20, 0]], spacing=5, size=1.0, reverse=True)
    assert rev[0][0][0] < rev[0][1][0]  # tip points back towards x = 0


def test_arrows_follow_a_corner():
    tris = circ.arrows_along([[0, 0], [10, 0], [10, 10]], spacing=4, size=1.0)
    last_tip, last_left, _ = tris[-1]
    assert last_tip[1] > last_left[1]  # heading north on the second leg


def test_batches_count_entities():
    forms = circ.lisp_solids(circ.arrows_along([[0, 0], [30, 0]], 5, 1), "CIRC-IN", 3)
    forms.append(circ.lisp_polyline([(0, 0), (1, 1)], "OBS", 8))
    b = circ.batch(forms, max_chars=500)
    assert len(b) > 1
    for expr in b:
        assert paren_balance(expr) == (0, None)


def test_closed_dialog_is_forgotten(monkeypatch, tmp_path):
    """A dialog closed outside the server (20 Oct 2026: splash, then the Library Explorer closed by hand) must not
    keep every later call answering 'dialog_pending'."""
    from civil3d_mcp import dialogs as dlg
    from civil3d_mcp import runner as runmod

    monkeypatch.setattr(runmod, "LOG_DIR", tmp_path)

    class FakeC3D:
        def cmdactive(self):
            return {"value": 0}

    r = runmod.Runner(FakeC3D())
    run = runmod.Run("DRIVEFOLLOW\n", "test", None, None)
    run.log_file = tmp_path / "r.jsonl"
    run.state = "dialog"
    run.pending = {"id": "x-d1", "snapshot": {"windows": [{"hwnd": 99, "title": "Please Wait", "controls": []}]}, "match": {}}
    r.active = run
    monkeypatch.setattr(dlg, "alive", lambda hwnds: False)
    out = r.wait(run, 1.0)
    assert out["status"] != "dialog_pending"
    assert run.pending is None
    assert any(a.get("dialog_id") == "x-d1" for a in run.answered)
