"""Catalogue matching and the runner's decision flow, with Civil 3D and Windows faked.

The fake dialogs reproduce the five pop-ups seen live on 18 Oct 2026.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ["CIVIL3D_MCP_HOME"] = tempfile.mkdtemp(prefix="c3dmcp-test-")

from civil3d_mcp import catalogue as catmod  # noqa: E402
from civil3d_mcp import runner as runmod  # noqa: E402
from civil3d_mcp import vt  # noqa: E402


def mk(title, texts=(), buttons=(), extra=()):
    ctrls = [{"type": "Text", "name": t, "rect": [0, 0, 1, 1]} for t in texts]
    ctrls += [{"type": "Button", "name": b, "rect": [0, 0, 1, 1]} for b in buttons]
    ctrls += list(extra)
    for i, c in enumerate(ctrls):
        c["id"] = f"c{i + 1}"
    return {"windows": [{"hwnd": 1, "title": title, "class": "#32770", "active": True, "controls": ctrls, "text": list(texts)}]}


D1_P = mk("Autodesk Vehicle Tracking", ["Do you wish to follow using the default vehicle (P - Passenger Car)?"], ["Yes", "No"])
D1_SU = mk("Autodesk Vehicle Tracking", ["Do you wish to follow using the default vehicle (SU - Single Unit)?"], ["Yes", "No"])
D5 = mk("Autodesk Vehicle Tracking", ["Follow has been unable to track the selected line closely without exceeding the current vehicle capabilities. Would you like Follow with the fastest speed?"], ["Yes", "No"])
D4 = mk("Settings", ["Start", "Direction", "Position", "Algorithm"], ["OK", "Cancel", "Help", "Make Default", "Reset"],
        [{"type": "ComboBox", "name": "", "label": "Direction", "value": "Forwards", "options": ["Forwards", "Reverse"], "rect": [0, 0, 1, 1]},
         {"type": "ComboBox", "name": "", "label": "Position", "value": "Automatic", "options": ["Automatic", "Manual"], "rect": [0, 0, 1, 1]},
         {"type": "CheckBox", "name": "Generate an editable path", "checked": False, "rect": [0, 0, 1, 1]}])
UNKNOWN = mk("AutoCAD Message", ["The drawing contains proxy objects."], ["OK"])


@pytest.fixture()
def cat(tmp_path):
    return catmod.Catalogue(tmp_path / "cat.json")


def test_fingerprint_ignores_dynamic_text():
    assert catmod.fingerprint(D1_P) == catmod.fingerprint(D1_SU)
    assert catmod.fingerprint(D1_P) != catmod.fingerprint(D4)


def test_seed_entries_need_live_confirmation(cat):
    m = cat.match(D1_P)
    assert m["status"] == "similar" and m["entry"]["id"] == "vt.default_vehicle"
    actions, why = catmod.resolve_answer(m, {"vt.default_vehicle": {"actions": [{"click": "Yes"}]}})
    assert actions is None and "never been confirmed" in why


def test_same_buttons_different_question_are_kept_apart(cat):
    cat.learn(D1_P, "vt.default_vehicle")
    cat.learn(D5, "vt.speed_reduction")
    assert cat.match(D1_SU)["entry"]["id"] == "vt.default_vehicle"
    assert cat.match(D5)["entry"]["id"] == "vt.speed_reduction"


def test_confirmed_entry_answered_only_with_task_answer_and_condition(cat):
    cat.learn(D1_P, "vt.default_vehicle")
    m = cat.match(D1_P)
    assert m["status"] == "exact"
    assert catmod.resolve_answer(m, None)[0] is None  # no answer from the task
    ans = vt.build_answers(vehicle="SU - Single Unit")
    assert catmod.resolve_answer(m, ans)[0] is None  # default is P, task wants SU -> Claude decides
    assert catmod.resolve_answer(cat.match(D1_SU), ans)[0] == [{"click": "Yes"}]


def test_judgement_needs_a_policy(cat):
    cat.learn(D5, "vt.speed_reduction")
    m = cat.match(D5)
    assert catmod.resolve_answer(m, vt.build_answers())[0] is None
    assert catmod.resolve_answer(m, vt.build_answers(speed_policy="accept_and_record"))[0] == [{"click": "Yes"}]


def test_changed_dialog_falls_back_to_claude(cat):
    cat.learn(D4, "vt.follow_settings")
    changed = mk("Settings", ["Start", "Direction", "Position", "Algorithm", "New option in 2028"], ["OK", "Cancel", "Help", "Make Default", "Reset"])
    m = cat.match(changed)
    assert m["status"] == "similar" and "controls differ" in m["reason"]


def test_unknown_dialog_is_kept_on_file(cat):
    m = cat.match(UNKNOWN)
    assert m["status"] == "unknown"
    cat.observe(UNKNOWN, m)
    assert any(e["id"].startswith("unknown.") for e in cat.entries)
    cat.learn(UNKNOWN, "acad.proxy_notice", description="Proxy objects notice", kind="judgement")
    assert not any(e["id"].startswith("unknown.") for e in cat.entries)


def test_preflight_lists_questions(cat):
    p = cat.preflight("vt_follow")
    assert len(p["ask_before_running"]) >= 5
    assert {d["id"] for d in p["known_dialogs"]} >= {"vt.default_vehicle", "vt.follow_settings", "vt.speed_reduction"}


# ---------------------------------------------------------------- runner flow
class FakeC3D:
    def __init__(self):
        self.cmd = 0

    def app(self):
        class A:
            HWND = 100
        return A()

    def _retry(self, fn, timeout=30):
        return fn()

    def cmdactive(self):
        return {"value": self.cmd, "active": []}

    def get_var(self, n):
        return "DRIVEFOLLOW"


@pytest.fixture()
def flow(monkeypatch, tmp_path):
    """Script of dialogs: each send pops the next dialogs; clicks close them."""
    state = {"queue": [], "open": None, "clicked": []}
    fake = FakeC3D()

    monkeypatch.setattr(runmod.dlg, "HAVE_WIN32", True)
    monkeypatch.setattr(runmod.dlg, "_pid_of", lambda h: 7)
    monkeypatch.setattr(runmod.dlg, "top_windows", lambda pid: [100])
    monkeypatch.setattr(runmod.dlg, "new_dialogs", lambda pid, main, base: {"modal": bool(state["open"]), "hwnds": [1] if state["open"] else []})
    monkeypatch.setattr(runmod.dlg, "snapshot", lambda hwnds: state["open"])

    def perform(snap, actions):
        state["clicked"].append(actions)
        state["open"] = state["queue"].pop(0) if state["queue"] else None
        if state["open"] is None:
            fake.cmd = 0
        return [str(a) for a in actions]

    monkeypatch.setattr(runmod.dlg, "perform", perform)
    monkeypatch.setattr(runmod, "_wait_closed", lambda h, timeout=3.0: True)

    class W:
        @staticmethod
        def IsWindow(h):
            return True
    monkeypatch.setattr(runmod.dlg, "win32gui", W, raising=False)

    r = runmod.Runner(fake)
    r.catalogue = catmod.Catalogue(tmp_path / "flow.json")
    sent = []

    def send(cmd):
        sent.append(cmd)
        if state["queue"]:
            fake.cmd = 1
            state["open"] = state["queue"].pop(0)
        else:
            fake.cmd = 0
    r._send = send
    return r, state, fake, sent


def test_first_run_brings_each_dialog_back_then_learns(flow):
    r, state, fake, sent = flow
    state["queue"] = [D1_P, D4, D5]
    run = r.start("DRIVEFOLLOW\n", answers=vt.build_answers(vehicle="P - Passenger Car", speed_policy="accept_and_record"),
                  on_finish=lambda: {"new_paths": ["ATR01"]})
    out = r.wait(run, 3)
    assert out["status"] == "dialog_pending" and out["catalogue"]["id"] == "vt.default_vehicle"
    out = r.act(out["dialog_id"], [{"click": "Yes"}], "task vehicle is P", learn={"id": "vt.default_vehicle"}, budget_s=3)
    assert out["status"] == "dialog_pending" and out["catalogue"]["id"] == "vt.follow_settings"
    out = r.act(out["dialog_id"], [{"select": {"label": "Direction", "value": "Forwards"}}, {"click": "OK"}], "stage is forwards",
                learn={"id": "vt.follow_settings"}, budget_s=3)
    assert out["status"] == "dialog_pending" and out["catalogue"]["id"] == "vt.speed_reduction"
    out = r.act(out["dialog_id"], [{"click": "Yes"}], "task accepts reduced speed", learn={"id": "vt.speed_reduction"},
                finding="speed reduced", budget_s=3)
    assert out["status"] == "finished" and out["result"] == {"new_paths": ["ATR01"]}
    assert out["findings"] == ["speed reduced"]
    assert Path(out["log"]).exists()


def test_second_run_is_answered_by_the_server(flow):
    r, state, fake, sent = flow
    for snap, eid in ((D1_P, "vt.default_vehicle"), (D4, "vt.follow_settings"), (D5, "vt.speed_reduction")):
        r.catalogue.learn(snap, eid)
    state["queue"] = [D1_P, D4, D5]
    run = r.start("DRIVEFOLLOW\n", answers=vt.build_answers(vehicle="P - Passenger Car", speed_policy="accept_and_record"))
    out = r.wait(run, 5)
    assert out["status"] == "finished"
    assert [a["entry"] for a in out["dialogs_answered"]] == ["vt.default_vehicle", "vt.follow_settings", "vt.speed_reduction"]
    assert all(a["by"].startswith("server") for a in out["dialogs_answered"])
    assert out["findings"] and "reduced the speed" in out["findings"][0]


def test_wrong_default_vehicle_comes_back(flow):
    r, state, fake, sent = flow
    r.catalogue.learn(D1_P, "vt.default_vehicle")
    state["queue"] = [D1_P]
    run = r.start("DRIVEFOLLOW\n", answers=vt.build_answers(vehicle="SU - Single Unit"))
    out = r.wait(run, 3)
    assert out["status"] == "dialog_pending"
    assert "does not hold" in out["catalogue"]["why_not_answered_automatically"]


def test_queued_input_is_typed_at_the_prompt(flow):
    r, state, fake, sent = flow

    def send(cmd):
        sent.append(cmd)
        fake.cmd = 1 if cmd.startswith("DRIVEFOLLOW") else 0
    r._send = send
    run = r.start("DRIVEFOLLOW\n", inputs=["5.000,0.000"])
    out = r.wait(run, 5)
    assert out["status"] == "finished"
    assert sent == ["DRIVEFOLLOW\n", "5.000,0.000\n"]


def test_one_run_at_a_time(flow):
    r, state, fake, sent = flow
    state["queue"] = [UNKNOWN]
    run = r.start("PURGE\n")
    assert r.wait(run, 2)["status"] == "dialog_pending"
    with pytest.raises(runmod.dlg.DialogError):
        r.start("ZOOM E\n")
