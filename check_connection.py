"""First-contact check for v0.2. Run with Civil 3D open and any drawing active:

    python check_connection.py

It only reads; nothing in the drawing is changed.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("CIVIL3D_MCP_HOME", str(HERE / "knowledge"))

from civil3d_mcp import civil, dialogs, vt  # noqa: E402
from civil3d_mcp.com_bridge import Civil3D  # noqa: E402


def step(label, fn):
    try:
        print(f"[ok]   {label}: {fn()}")
    except Exception as e:  # noqa: BLE001
        print(f"[fail] {label}: {type(e).__name__}: {e}")


c = Civil3D()
step("Server code", lambda: f"v0.2 from {HERE}")
step("Civil 3D session", lambda: c.call(c.status))
step("Layers", lambda: len(c.call(c.layers)))
step("Civil objects", lambda: c.call(civil.inventory, c))
step("Vehicle Tracking paths", lambda: len(c.call(vt.list_paths, c)))
step("Win32 window access", lambda: dialogs.HAVE_WIN32 or (_ for _ in ()).throw(RuntimeError("pywin32 missing")))
step("UI Automation (pywinauto)", lambda: dialogs.HAVE_UIA or (_ for _ in ()).throw(RuntimeError("run: python -m pip install pywinauto")))


def read_main_window():
    app = c.call(c.app)
    h = int(c.call(lambda: app.HWND))
    snap = dialogs.snapshot([h])
    w = snap["windows"][0]
    return f"read '{w['title'][:40]}', {len(w['controls'])} controls"


step("Read Civil 3D's own window through UI Automation", read_main_window)
step("Dialog catalogue", lambda: f"{len(c.runner.catalogue.entries)} entries at {c.runner.catalogue.path}")
print("\nIf every line says [ok], point Claude Desktop at run_server.py (see README).")
