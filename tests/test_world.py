"""Unit tests for the WorldCache sharing primitive (world.py). Pure — the
FurryWorld build is stubbed, so no game files."""

import os

from furrifier_fo4 import world as W


def test_world_key_lowercases_plugins_and_handles_none():
    assert W._world_key("s", None, None) == ("s", "", None, None)
    assert W._world_key("s", "D", ["A.esp", "B.ESP"]) == \
        ("s", "D", ("a.esp", "b.esp"), None)
    # The resource fingerprint is part of the identity.
    assert W._world_key("s", None, None, ("f", 1))[3] == ("f", 1)


def test_cache_reuses_by_key_rebuilds_on_change_closes_old(monkeypatch):
    built = []

    class _Fake:
        def __init__(self, scheme, data_dir=None, plugins=None, progress=None):
            self.scheme = scheme
            self.closed = False
            built.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(W, "FurryWorld", _Fake)
    cache = W.WorldCache()

    a = cache.get_or_build("s1", None, ["A.esp"])
    b = cache.get_or_build("s1", None, ["a.esp"])     # same key (case-insensitive)
    assert a is b and len(built) == 1                 # reused, not rebuilt

    d = cache.get_or_build("s2", None, ["A.esp"])     # scheme change -> rebuild
    assert d is not a and len(built) == 2
    assert a.closed and not d.closed                  # old world closed

    cache.close()
    assert d.closed


def test_cache_rebuilds_when_resource_files_change(monkeypatch):
    """A scheme/race TOML edit (a changed fingerprint) rebuilds the world even
    though scheme name, data dir, and plugin list are unchanged."""
    built = []

    class _Fake:
        def __init__(self, scheme, data_dir=None, plugins=None, progress=None):
            self.closed = False
            built.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(W, "FurryWorld", _Fake)
    fp = {"v": ("races/a.toml", 1)}
    monkeypatch.setattr(W, "_resource_fingerprint",
                        lambda scheme, races_dir=None: fp["v"])
    cache = W.WorldCache()

    a = cache.get_or_build("s1", None, ["A.esp"])
    b = cache.get_or_build("s1", None, ["A.esp"])
    assert a is b and len(built) == 1          # files unchanged -> reuse

    fp["v"] = ("races/a.toml", 2)              # a TOML was edited
    c = cache.get_or_build("s1", None, ["A.esp"])
    assert c is not a and len(built) == 2      # rebuilt on the edit
    assert a.closed and not c.closed           # old world released

    cache.close()


def test_resource_fingerprint_reflects_edits_and_new_files(tmp_path,
                                                            monkeypatch):
    """The fingerprint changes when a race TOML's mtime changes or a file is
    added/removed — driven off the real scheme + race file paths."""
    races = tmp_path / "races"
    races.mkdir()
    (races / "a.toml").write_text("x = 1")
    scheme_file = tmp_path / "s1.toml"
    scheme_file.write_text("y = 2")

    monkeypatch.setattr(W, "default_races_dir", lambda: races)
    monkeypatch.setattr("furrifier_fo4.loader.scheme_source_paths",
                        lambda name: [scheme_file])

    fp1 = W._resource_fingerprint("s1")
    os.utime(races / "a.toml", ns=(0, 99_000_000))   # touch -> new mtime
    fp2 = W._resource_fingerprint("s1")
    assert fp2 != fp1                                  # edit detected

    (races / "b.toml").write_text("z = 3")
    fp3 = W._resource_fingerprint("s1")
    assert fp3 != fp2                                  # new catalog file detected

    (races / "b.toml").unlink()
    assert W._resource_fingerprint("s1") == fp2        # removal returns to fp2
