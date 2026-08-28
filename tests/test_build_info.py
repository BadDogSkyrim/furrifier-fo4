"""Tests for version / build identity reporting.

The build number's whole job is letting a user and a developer agree on
which kit is in front of them, so the failure that matters is a source
tree or a stale stamp claiming a build number it doesn't have.

Mirrors furrifier/tests/unit/test_build_info.py — the two furrifiers
report their identity the same way on purpose.
"""

import importlib
import json
import re
import sys
from pathlib import Path

from furrifier_fo4 import __version__, build_info

REPO = Path(__file__).resolve().parents[1]


def _reload():
    return importlib.reload(build_info)


def test_dev_checkout_reports_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    mod = _reload()
    assert mod.BUILD is None
    assert mod.version_string() == f"v{__version__} (dev)"


def test_dev_checkout_ignores_a_stale_stamp(monkeypatch):
    """A stamp left behind by an earlier build must not be reported by a
    source run — that would be the exact ambiguity we're removing."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    stamp = type(sys)("furrifier_fo4._build_stamp")
    stamp.BUILD = 99
    monkeypatch.setitem(sys.modules, "furrifier_fo4._build_stamp", stamp)
    assert _reload()._build_number() is None


def test_frozen_reports_the_number(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    stamp = type(sys)("furrifier_fo4._build_stamp")
    stamp.BUILD = 7
    monkeypatch.setitem(sys.modules, "furrifier_fo4._build_stamp", stamp)

    mod = _reload()
    assert mod.BUILD == 7
    assert mod.version_string() == f"v{__version__} build 7"


def test_frozen_without_a_stamp_falls_back_to_dev(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setitem(sys.modules, "furrifier_fo4._build_stamp", None)
    # A None entry in sys.modules makes the import raise ImportError.
    mod = _reload()
    assert mod.BUILD is None
    assert "(dev)" in mod.version_string()


def test_version_is_three_part_semantic():
    """The build number is a separate counter, so the version itself
    carries no build component — a fourth field would mean the old
    scheme has crept back."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), (
        f"{__version__!r} is not major.minor.patch — the build number "
        f"belongs in build_number.json, not in the version string"
    )


def test_package_declares_its_version_exactly_once():
    """The package carried two disagreeing __version__ declarations once
    (__init__.py said 0.1.0 while _version.py said 1.1.4). Whichever one
    a reader finds first would be wrong."""
    pkg = REPO / "src" / "furrifier_fo4"
    declared = {
        path.relative_to(REPO).as_posix()
        for path in pkg.rglob("*.py")
        if re.search(r'^__version__\s*=', path.read_text(encoding="utf-8"),
                     re.M)
    }
    assert declared == {"src/furrifier_fo4/__init__.py"}, (
        f"__version__ declared in {sorted(declared)}"
    )


def test_counter_file_tracks_the_current_version():
    """build_number.json must describe the version actually shipping;
    a mismatch means the next build silently resets the count."""
    path = REPO / "build_number.json"
    if not path.exists():
        return  # never built in this checkout
    state = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(state["build"], int)
    assert state["version"] == __version__, (
        f"build_number.json is on {state['version']!r} but the package is "
        f"{__version__!r} — the next build will reset to 0"
    )


def test_spec_parses_the_same_version():
    """The spec reads __version__ by regex rather than importing. Guard
    that the regex still matches the real declaration."""
    init_src = (REPO / "src" / "furrifier_fo4" / "__init__.py").read_text(
        encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_src, re.M)
    assert m and m.group(1) == __version__


def test_spec_does_not_rewrite_the_version():
    """The old scheme edited __version__ in place on every build, which
    is what put a build counter in the third component."""
    spec = (REPO / "furrify_fo4.spec").read_text(encoding="utf-8")
    assert "__version__ = " not in spec, (
        "the spec writes __version__ back — the version is hand-edited now"
    )


def test_spec_persists_the_counter_after_collect():
    """The counter must be written only once a kit actually exists.

    The stamp has to be generated before Analysis, but PyInstaller can
    fail later — most often refusing to clear dist/ while the previously
    built kit is still running. Bumping the file up front burned a
    number that no build ever shipped under.
    """
    spec = (REPO / "furrify_fo4.spec").read_text(encoding="utf-8")
    collect_at = spec.index("coll = COLLECT(")
    writes = [i for i in range(len(spec))
              if spec.startswith("_counter_path.write_text", i)]
    assert len(writes) == 1, "counter should be written exactly once"
    assert writes[0] > collect_at, (
        "build_number.json is written before COLLECT — a failed build "
        "would burn a number no kit shipped under"
    )


def teardown_module(module):
    # Leave the module in its real state for everything else.
    importlib.reload(build_info)
