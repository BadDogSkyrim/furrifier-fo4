"""Unit tests for the WorldCache sharing primitive (world.py). Pure — the
FurryWorld build is stubbed, so no game files."""

from furrifier_fo4 import world as W


def test_world_key_lowercases_plugins_and_handles_none():
    assert W._world_key("s", None, None) == ("s", "", None)
    assert W._world_key("s", "D", ["A.esp", "B.ESP"]) == ("s", "D", ("a.esp", "b.esp"))


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
