"""v0.7: blocker watchdog rules (26 Oct 2026)."""
from civil3d_mcp.watchdog import civil_pids, classify


def _w(title, text=(), buttons=(), inputs=(), cls="#32770"):
    ctrls = [{"type": "Button", "name": b} for b in buttons] + [{"type": t, "name": n} for t, n in inputs]
    return {"title": title, "class": cls, "text": list(text), "controls": ctrls}


def test_drawing_recovery_is_safe_notice():
    w = _w("Drawing Recovery", ["In your previous work session, the program terminated unexpectedly. You can restore "
                                "unsaved changes from a backup file by using the Drawing Recovery Manager."],
           cls="Chrome_WidgetWin_1", inputs=[("CheckBox", "Do not show me this message again")])
    assert classify(w) == ("safe_notice", "WM_CLOSE")


def test_plain_ok_notice():
    assert classify(_w("AutoCAD Message", ["The operation completed."], ["OK"])) == ("notice", "OK")


def test_cancelable():
    assert classify(_w("Select Template", ["Choose a template"], ["Open", "Cancel"])) == ("cancelable", "Cancel")


def test_protected_never_answered():
    for title, text, buttons in [
        ("AutoCAD", ["Save changes to Drawing1.dwg?"], ["Yes", "No", "Cancel"]),
        ("Drawing File Exists", ["launch_test.dwg already exists. Do you want to replace it?"], ["Yes", "No"]),
        ("Autodesk Licensing", ["Your license has expired"], ["OK"]),
        ("Sign In", ["Sign in to your Autodesk account"], ["Cancel"]),
    ]:
        assert classify(_w(title, text, buttons))[0] == "protected", title


def test_palette_without_buttons_is_not_closed():
    # a floating palette has no buttons and is not a standard dialog: it must not read as a notice
    assert classify(_w("TOOL PALETTES", [], [], cls="Afx:00007FF6:8")) == ("unknown", None)


def test_choices_without_cancel_are_unknown():
    assert classify(_w("Vehicle Tracking", ["Follow with default vehicle?"], ["Yes", "No"]))[0] == "unknown"


def test_civil_pids_follows_children():
    procs = {10: (1, "explorer.exe"), 20: (10, "acad.exe"), 30: (20, "msedgewebview2.exe"),
             31: (30, "msedgewebview2.exe"), 40: (10, "chrome.exe"), 41: (40, "msedgewebview2.exe")}
    assert civil_pids(procs) == {20: "acad.exe", 30: "msedgewebview2.exe", 31: "msedgewebview2.exe"}
