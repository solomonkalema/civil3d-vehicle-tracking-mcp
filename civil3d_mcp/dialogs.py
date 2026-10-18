"""Find, read and operate Civil 3D dialogs without the screen or the mouse.

Reading uses Windows UI Automation (pywinauto, "uia" backend) when it is
installed, and falls back to plain win32 messages otherwise. Actions use UIA
patterns (Invoke, Toggle, SelectionItem, ExpandCollapse, Value) or win32
messages (BM_CLICK, CB_SETCURSEL), never mouse clicks, so the person can keep
working in other programs while a run is answered.

Nothing in this module talks to Civil 3D over COM: a modal dialog blocks COM,
and this code has to keep working exactly then.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    import win32con
    import win32gui
    import win32process

    HAVE_WIN32 = True
except ImportError:
    HAVE_WIN32 = False

try:
    from pywinauto import Desktop  # type: ignore

    HAVE_UIA = HAVE_WIN32
except Exception:  # pywinauto missing or failing to import
    HAVE_UIA = False

BM_GETCHECK = 0x00F0
BM_CLICK = 0x00F5
CB_GETCOUNT = 0x0146
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
CB_SETCURSEL = 0x014E
CBN_SELCHANGE = 1
BS_TYPEMASK = 0x0F
_BS = {2: "CheckBox", 3: "CheckBox", 5: "CheckBox", 6: "CheckBox", 4: "RadioButton", 9: "RadioButton", 7: "Group"}

_MAX_CONTROLS = 250


class DialogError(RuntimeError):
    pass


# =============================================================================
# finding dialogs
# =============================================================================
def _pid_of(hwnd: int) -> int:
    return win32process.GetWindowThreadProcessId(hwnd)[1]


def top_windows(pid: int) -> list[int]:
    """Visible top-level windows of a process."""
    out: list[int] = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and _pid_of(h) == pid:
                out.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def _is_dialog_like(h: int) -> bool:
    try:
        l, t, r, b = win32gui.GetWindowRect(h)
        if r - l < 120 or b - t < 60:
            return False  # tooltips, drag images, tiny popups
        cls = win32gui.GetClassName(h)
        if cls in ("tooltips_class32", "#32768", "SysShadow", "IME", "MSCTFIME UI"):
            return False
        return True
    except Exception:
        return False


def new_dialogs(pid: int, main_hwnd: int, baseline: set[int]) -> dict:
    """Windows that appeared since the run started.

    A dialog group is reported when the main window is disabled (a modal dialog
    is up) or a new #32770 dialog is visible. Floating palettes that existed
    before the run are part of the baseline and ignored.
    """
    if not HAVE_WIN32:
        return {"modal": False, "hwnds": []}
    modal = not win32gui.IsWindowEnabled(main_hwnd)
    tops = top_windows(pid)
    fresh = [h for h in tops if h != main_hwnd and h not in baseline and _is_dialog_like(h)]
    if modal and not fresh:
        # a modal is up but it predates the run (e.g. left over): report whatever is enabled
        fresh = [h for h in tops if h != main_hwnd and _is_dialog_like(h) and win32gui.IsWindowEnabled(h)]
    if not modal:
        fresh = [h for h in fresh if win32gui.GetClassName(h) == "#32770" and win32gui.IsWindowEnabled(h)]
    # put the enabled (active) window first: that is the one waiting for an answer
    fresh.sort(key=lambda h: (not win32gui.IsWindowEnabled(h), h))
    return {"modal": modal, "hwnds": fresh}


# =============================================================================
# reading
# =============================================================================
def snapshot(hwnds: list[int]) -> dict:
    """Read every window of a dialog group. UIA first; win32 for anything UIA cannot read."""
    windows = []
    n = 0
    for h in hwnds:
        w = None
        err = "UI Automation unavailable (install pywinauto)"
        if HAVE_UIA:
            try:
                w = _read_uia(h, n)
            except Exception as e:  # keep going with win32
                w = None
                err = f"UI Automation could not read this window ({type(e).__name__}); win32 fallback used"
        if w is None:
            w = _read_win32(h, n)
            if HAVE_UIA:
                w.setdefault("unreadable", []).append(err)
        n += len(w["controls"])
        windows.append(w)
    return {"windows": windows, "read_at": time.time()}


def _rect(r) -> list[int]:
    return [int(r.left), int(r.top), int(r.right), int(r.bottom)]


def _read_uia(hwnd: int, start_id: int) -> dict:
    win = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    info = win.element_info
    w: dict[str, Any] = {
        "hwnd": hwnd,
        "title": info.name or win32gui.GetWindowText(hwnd),
        "class": win32gui.GetClassName(hwnd),
        "active": bool(win32gui.IsWindowEnabled(hwnd)),
        "rect": _rect(info.rectangle),
        "controls": [],
        "text": [],
    }
    ctrls = []
    unreadable = 0
    for i, c in enumerate(win.descendants()):
        if i >= _MAX_CONTROLS:
            w.setdefault("unreadable", []).append(f"more than {_MAX_CONTROLS} controls; the rest were not read")
            break
        ei = c.element_info
        t = ei.control_type or ""
        name = (ei.name or "").strip()
        row: dict[str, Any] = {"type": t, "name": name, "rect": _rect(ei.rectangle), "enabled": bool(ei.enabled)}
        if ei.automation_id:
            row["auto_id"] = ei.automation_id
        try:
            if t == "ComboBox":
                try:
                    row["value"] = c.selected_text()
                except Exception:
                    row["value"] = _uia_value(c)
                try:
                    opts = [x for x in c.texts() if x]
                    if opts and opts != [row.get("value")]:
                        row["options"] = opts[:40]
                except Exception:
                    pass
            elif t == "CheckBox":
                row["checked"] = {0: False, 1: True, 2: "mixed"}.get(c.get_toggle_state(), None)
            elif t == "RadioButton":
                row["selected"] = bool(c.is_selected())
            elif t in ("Edit", "Spinner"):
                row["value"] = _uia_value(c)
            elif t in ("TreeItem", "ListItem", "DataItem"):
                try:
                    row["selected"] = bool(c.is_selected())
                except Exception:
                    pass
                if t == "TreeItem":
                    try:
                        row["expanded"] = bool(c.is_expanded())
                    except Exception:
                        pass
                    row["depth"] = _tree_depth(c)
            elif t == "Text" and name:
                w["text"].append(name)
        except Exception:
            pass
        if t in ("Pane", "Custom", "Image") and not name:
            unreadable += 1
            continue  # containers and drawn areas: counted, not listed
        if t in ("TitleBar", "ScrollBar", "Thumb", "Separator") or (t == "Button" and name in ("Close", "Minimize", "Maximize", "Restore")):
            continue
        ctrls.append(row)
    _label(ctrls)
    for k, row in enumerate(ctrls):
        row["id"] = f"c{start_id + k + 1}"
    w["controls"] = ctrls
    if unreadable:
        w.setdefault("unreadable", []).append(
            f"{unreadable} drawn area(s) with no readable text (e.g. a diagram); use c3d_dialog_image to see them"
        )
    return w


def _uia_value(c) -> str | None:
    try:
        return c.get_value()
    except Exception:
        try:
            return c.window_text()
        except Exception:
            return None


def _tree_depth(c) -> int:
    d = 0
    p = c
    try:
        while True:
            p = p.parent()
            if p is None or p.element_info.control_type != "TreeItem":
                return d
            d += 1
    except Exception:
        return d


def _label(ctrls: list[dict]) -> None:
    """Give unnamed inputs the text label to their left (same row) or just above."""
    texts = [c for c in ctrls if c["type"] == "Text" and c.get("name")]
    for c in ctrls:
        if c["type"] not in ("ComboBox", "Edit", "Spinner", "List", "Tree", "Slider"):
            continue
        l, t, r, b = c["rect"]
        best, score = None, 1e9
        for x in texts:
            xl, xt, xr, xb = x["rect"]
            if xb >= t - 2 and xt <= b + 2 and xr <= l + 4:  # same row, to the left
                s = l - xr
            elif xb <= t + 2 and t - xb < 30 and abs(xl - l) < 60:  # directly above
                s = 200 + (t - xb)
            else:
                continue
            if s < score:
                best, score = x, s
        if best is not None:
            c["label"] = best["name"].rstrip(":")
        elif c.get("name"):
            c["label"] = c["name"]


def _read_win32(hwnd: int, start_id: int) -> dict:
    w: dict[str, Any] = {
        "hwnd": hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "class": win32gui.GetClassName(hwnd),
        "active": bool(win32gui.IsWindowEnabled(hwnd)),
        "rect": list(win32gui.GetWindowRect(hwnd)),
        "controls": [],
        "text": [],
        "backend": "win32",
    }
    kids: list[int] = []
    win32gui.EnumChildWindows(hwnd, lambda h, _: kids.append(h) or True, None)
    ctrls = []
    for h in kids[:_MAX_CONTROLS]:
        if not win32gui.IsWindowVisible(h):
            continue
        cls = win32gui.GetClassName(h)
        txt = win32gui.GetWindowText(h).strip()
        row: dict[str, Any] = {"hwnd": h, "rect": list(win32gui.GetWindowRect(h)), "enabled": bool(win32gui.IsWindowEnabled(h)), "name": txt}
        if cls == "Button":
            style = win32gui.GetWindowLong(h, win32con.GWL_STYLE) & BS_TYPEMASK
            row["type"] = _BS.get(style, "Button")
            if row["type"] in ("CheckBox", "RadioButton"):
                st = win32gui.SendMessage(h, BM_GETCHECK, 0, 0)
                row["checked" if row["type"] == "CheckBox" else "selected"] = st == 1
        elif cls == "Static":
            if not txt:
                continue
            row["type"] = "Text"
            w["text"].append(txt)
        elif cls == "Edit":
            row["type"] = "Edit"
            row["value"] = _wm_gettext(h)
            row["name"] = ""
        elif cls.startswith("ComboBox"):
            row["type"] = "ComboBox"
            opts = _combo_items(h)
            sel = win32gui.SendMessage(h, CB_GETCURSEL, 0, 0)
            row["value"] = opts[sel] if 0 <= sel < len(opts) else txt
            row["options"] = opts[:40]
            row["name"] = ""
        elif cls in ("SysTreeView32", "SysListView32"):
            row["type"] = "Tree" if cls == "SysTreeView32" else "List"
            w.setdefault("unreadable", []).append(f"{row['type']} contents need UI Automation (install pywinauto)")
        else:
            continue
        ctrls.append(row)
    _label(ctrls)
    for k, row in enumerate(ctrls):
        row["id"] = f"c{start_id + k + 1}"
    w["controls"] = ctrls
    return w


def _wm_gettext(h: int) -> str:
    try:
        return win32gui.GetWindowText(h)
    except Exception:
        return ""


def _combo_items(h: int) -> list[str]:
    import ctypes

    out = []
    n = win32gui.SendMessage(h, CB_GETCOUNT, 0, 0)
    for i in range(max(0, min(n, 60))):
        ln = win32gui.SendMessage(h, CB_GETLBTEXTLEN, i, 0)
        if ln <= 0:
            out.append("")
            continue
        buf = ctypes.create_unicode_buffer(ln + 1)
        ctypes.windll.user32.SendMessageW(h, CB_GETLBTEXT, i, buf)
        out.append(buf.value)
    return out


# =============================================================================
# acting
# =============================================================================
def _find(snap: dict, ref: dict | str, types: tuple[str, ...] | None = None) -> tuple[dict, dict]:
    """Find a control by id ('c7'), by label, or by name. Returns (window, control)."""
    if isinstance(ref, str):
        ref = {"id": ref} if ref[:1] == "c" and ref[1:].isdigit() else {"name": ref}
    want_id = ref.get("id")
    want = (ref.get("label") or ref.get("name") or "").strip().lower().rstrip(":")
    hits = []
    for w in snap["windows"]:
        for c in w["controls"]:
            if types and c["type"] not in types:
                continue
            if want_id and c.get("id") == want_id:
                return w, c
            if want and want in ((c.get("label") or "").lower().rstrip(":"), (c.get("name") or "").lower().rstrip(":").replace("&", "")):
                hits.append((w, c))
    if not hits:
        raise DialogError(f"No control matching {ref} in the dialog.")
    # prefer an enabled control in the active (first) window
    hits.sort(key=lambda wc: (not wc[1].get("enabled", True), snap["windows"].index(wc[0])))
    return hits[0]


def _uia(w: dict, c: dict):
    """Re-find a control live in UIA by type, name and position (ids are per snapshot)."""
    win = Desktop(backend="uia").window(handle=w["hwnd"]).wrapper_object()
    for x in win.descendants():
        ei = x.element_info
        if ei.control_type == c["type"] and (ei.name or "").strip() == c.get("name", "") and _rect(ei.rectangle) == c["rect"]:
            return x
    for x in win.descendants():  # moved slightly (repaint): match type and name only
        ei = x.element_info
        if ei.control_type == c["type"] and (ei.name or "").strip() == c.get("name", "") and c.get("name"):
            return x
    raise DialogError(f"Control {c.get('id')} ({c['type']} '{c.get('name') or c.get('label')}') is no longer in the dialog.")


def perform(snap: dict, actions: list[dict]) -> list[str]:
    """Run actions in order. Each is one of:
    {"click": "OK"}                          button by name/label/id
    {"select": {"label": "Direction", "value": "Forwards"}}  combo option, or a radio button (value true)
    {"check": {"label": "Prompt for scale", "value": false}}  checkbox
    {"type": {"label": "Split line into lengths of", "value": "2.5"}}  text field
    {"tree": ["South African Design Vehicles", "SANRAL", "SU - Single Unit"]}  select a tree item by path
    {"wait": 0.5}
    """
    done = []
    for a in actions:
        (verb, arg), = a.items()
        if verb == "wait":
            time.sleep(float(arg))
            done.append(f"waited {arg}s")
            continue
        if verb == "tree":
            done.append(_tree_select(snap, list(arg)))
            continue
        ref = arg if isinstance(arg, (str, dict)) else str(arg)
        if verb == "click":
            w, c = _find(snap, ref, ("Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "DataItem", "TreeItem"))
            _click(w, c)
            done.append(f"clicked {c.get('name') or c.get('label')}")
        elif verb == "select":
            val = arg.get("value")
            w, c = _find(snap, arg, ("ComboBox", "RadioButton", "ListItem", "List"))
            _select(w, c, val)
            done.append(f"set {c.get('label') or c.get('name')} = {val}")
        elif verb == "check":
            val = bool(arg.get("value", True))
            w, c = _find(snap, arg, ("CheckBox",))
            _check(w, c, val)
            done.append(f"{'ticked' if val else 'unticked'} {c.get('name')}")
        elif verb == "type":
            w, c = _find(snap, arg, ("Edit", "Spinner", "ComboBox"))
            _type(w, c, str(arg.get("value", "")))
            done.append(f"typed {arg.get('value')!r} into {c.get('label') or c.get('name')}")
        else:
            raise DialogError(f"Unknown action '{verb}'. Use click, select, check, type, tree or wait.")
        time.sleep(0.25)
    return done


def _click(w: dict, c: dict) -> None:
    if "hwnd" in c:
        win32gui.PostMessage(c["hwnd"], BM_CLICK, 0, 0)
        return
    x = _uia(w, c)
    try:
        x.invoke()
    except Exception:
        try:
            x.select()
        except Exception:
            x.click()  # UIA click: still no mouse movement for buttons


def _select(w: dict, c: dict, val) -> None:
    if c["type"] == "RadioButton":
        if not val:
            raise DialogError("A radio button can only be selected; select the other option instead.")
        if "hwnd" in c:
            win32gui.PostMessage(c["hwnd"], BM_CLICK, 0, 0)
        else:
            _uia(w, c).select()
        return
    if c["type"] == "ComboBox":
        opts = c.get("options") or []
        if opts and str(val) not in opts:
            low = {o.lower(): o for o in opts}
            if str(val).lower() not in low:
                raise DialogError(f"'{val}' is not one of {opts}.")
            val = low[str(val).lower()]
        if "hwnd" in c:
            idx = opts.index(val)
            win32gui.SendMessage(c["hwnd"], CB_SETCURSEL, idx, 0)
            parent = win32gui.GetParent(c["hwnd"])
            cid = win32gui.GetDlgCtrlID(c["hwnd"])
            win32gui.SendMessage(parent, win32con.WM_COMMAND, (CBN_SELCHANGE << 16) | cid, c["hwnd"])
            return
        _uia(w, c).select(str(val))
        return
    _uia(w, c).select()


def _check(w: dict, c: dict, val: bool) -> None:
    if c.get("checked") == val:
        return
    if "hwnd" in c:
        win32gui.PostMessage(c["hwnd"], BM_CLICK, 0, 0)
        return
    x = _uia(w, c)
    for _ in range(3):
        if bool(x.get_toggle_state() == 1) == val:
            return
        x.toggle()


def _type(w: dict, c: dict, text: str) -> None:
    if "hwnd" in c:
        win32gui.SendMessage(c["hwnd"], win32con.WM_SETTEXT, 0, text)
        return
    x = _uia(w, c)
    try:
        x.set_edit_text(text)
    except Exception:
        x.iface_value.SetValue(text)


def _tree_select(snap: dict, path: list[str]) -> str:
    if not HAVE_UIA:
        raise DialogError("Selecting in a tree needs UI Automation: install pywinauto.")
    for w in snap["windows"]:
        win = Desktop(backend="uia").window(handle=w["hwnd"]).wrapper_object()
        trees = [x for x in win.descendants() if x.element_info.control_type == "Tree"]
        for tree in trees:
            level = [x for x in tree.children() if x.element_info.control_type == "TreeItem"]
            node = None
            for step in path:
                node = next((x for x in level if (x.element_info.name or "").strip().lower() == step.strip().lower()), None)
                if node is None:
                    # vehicle rows can carry extra text (dimensions); accept a prefix match
                    node = next((x for x in level if (x.element_info.name or "").strip().lower().startswith(step.strip().lower())), None)
                if node is None:
                    break
                if step is not path[-1]:
                    try:
                        node.expand()
                    except Exception:
                        pass
                    time.sleep(0.3)
                    level = [x for x in node.children() if x.element_info.control_type == "TreeItem"]
            if node is not None:
                node.select()  # SelectionItem pattern; never a mouse click
                return f"selected {' > '.join(path)}"
        raise DialogError(f"Could not find {' > '.join(path)} in any tree of the dialog.")
    raise DialogError("No tree in the dialog.")


# =============================================================================
# picture of a dialog (no screen capture: PrintWindow draws the window itself)
# =============================================================================
def capture(hwnd: int, out: Path) -> Path:
    import win32ui
    from PIL import Image

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    wd, ht = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, wd, ht)
    mem.SelectObject(bmp)
    import ctypes

    ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)  # 2 = PW_RENDERFULLCONTENT
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
