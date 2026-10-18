"""Start Civil 3D MCP from this folder (alternative to `python -m civil3d_mcp.server`)."""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("CIVIL3D_MCP_HOME", str(HERE / "knowledge"))

from civil3d_mcp.server import main  # noqa: E402

main()
