"""Helpers for finding the PyNifly package + the trifile.py module
across dev and frozen-kit modes.

Dev mode: `pyn` lives at ``C:\\Modding\\PyNifly\\io_scene_nifly\\pyn``
(Hugh's checkout). Modules that import it call ``ensure_dev_path()``
once at import time so the parent folder is on ``sys.path``.

Frozen mode: PyInstaller bundles `pyn` as a regular package via the
spec's ``pathex`` and ships ``NiflyDLL.dll`` next to it; the dev path
doesn't exist on user machines, so ``ensure_dev_path()`` no-ops.

The kit also ships ``tri/trifile.py`` (PyNifly's TriFile loader)
loose under ``_internal/tri/`` because ``tri/__init__.py`` imports
``bpy`` and we have to bypass it via ``importlib``.
``trifile_path()`` returns the right path for whichever mode.
"""
from __future__ import annotations

import sys
from pathlib import Path


_DEV_ROOT = Path(r"C:\Modding\PyNifly\io_scene_nifly")


def ensure_dev_path() -> None:
    """Add the dev PyNifly checkout to ``sys.path`` if it exists. No-op
    in a frozen kit (the bundled `pyn` is already importable)."""
    p = str(_DEV_ROOT)
    if _DEV_ROOT.is_dir() and p not in sys.path:
        sys.path.insert(0, p)


def trifile_path() -> str:
    """Absolute path to PyNifly's ``trifile.py`` for ``importlib`` to
    load. Frozen: ``_internal/tri/trifile.py``. Dev: the checkout's
    ``tri/trifile.py``. Caller is responsible for handling the
    file-not-found case (rare — only happens if the kit was unzipped
    incompletely)."""
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "tri" / "trifile.py")  # type: ignore[attr-defined]
    return str(_DEV_ROOT / "tri" / "trifile.py")
