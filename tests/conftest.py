"""Put the package src/ on sys.path so tests run without an editable install."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# --- test prerequisites banner ---------------------------------------
# Reports missing games/mods above the run. Imported rather than defined
# in the repo-root conftest because this package has its own
# pyproject.toml, which makes IT the pytest rootdir when its suite runs
# directly — and the root conftest is then never loaded.
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
from test_prereqs import pytest_report_header  # noqa: E402,F401
