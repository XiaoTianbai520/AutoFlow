"""Keep bundled Qt DLL directories ahead of unrelated Qt installations on PATH."""

import os
import sys
from pathlib import Path

_handles = []
if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    root = Path(sys._MEIPASS)
    for folder in (root / "PySide6", root / "shiboken6", root):
        if folder.is_dir():
            _handles.append(os.add_dll_directory(str(folder)))
    os.environ["PATH"] = os.pathsep.join(str(folder) for folder in (root / "PySide6", root / "shiboken6", root)) + os.pathsep + os.environ.get("PATH", "")
