"""Version and build identity, for logs and the GUI.

The build number is a plain integer that increments on every build and
resets to 0 when ``__version__`` changes. Its only job is to let a user
and a developer agree on *which* build is in front of them: "I'm on
1.2.0 build 7" is unambiguous, which is all we need.

The counter lives in ``build_number.json`` at the repo root (checked in,
so it survives clean checkouts and is visible in history). The spec
bumps it at build time and bakes the result into ``_build_stamp.py``,
which is packed into the frozen kit.

Running from source reports ``dev`` rather than a number — a source tree
isn't any particular build, and claiming one would be the exact
confusion the number exists to prevent.

Mirrors furrifier/src/furrifier/build_info.py.
"""

from __future__ import annotations

import sys

from . import __version__

VERSION = __version__


def _build_number() -> int | None:
    """The baked-in build number, or None when not running frozen.

    Only consulted in a frozen kit. Building writes the stamp into the
    source tree and leaves it there, so a dev checkout that has ever
    been built would otherwise keep reporting a stale build number.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        from ._build_stamp import BUILD  # type: ignore
    except Exception:
        return None
    return BUILD


BUILD = _build_number()


def version_string() -> str:
    """One line identifying this build:

        v1.2.0 build 7     — a frozen kit
        v1.2.0 (dev)       — running from source
    """
    if BUILD is None:
        return f"v{VERSION} (dev)"
    return f"v{VERSION} build {BUILD}"
