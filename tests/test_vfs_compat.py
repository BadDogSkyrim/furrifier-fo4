"""Mod Organizer 2 compatibility: never decide a game file exists by stat.

MO2's virtual filesystem is visible to directory enumeration and to
`open()`, but not to the call CPython uses for `stat`. Anything supplied
by a mod rather than sitting physically in the Data folder therefore
enumerates fine, opens fine, and reports "does not exist" to
`exists()` / `is_file()` / `is_dir()`.

The Skyrim furrifier was unusable under MO2 for exactly this reason, in
five separate places, and each one looked like a different bug. These
tests pin the FO4 equivalents. usvfs can't run in a test, so we blind
stat directly and assert the code still finds files that open.

See the Skyrim originals: esplib/tests/test_vfs_helpers.py and
furrifier/tests/facegen/test_asset_resolver.py.
"""

import logging
import os

import pytest

from furrifier_fo4.facegen.assets import AssetResolver


@pytest.fixture
def blind_stat(monkeypatch):
    """Deny stat for paths containing a marker, leaving open() and
    scandir working — what usvfs effectively does for mod-supplied
    entries."""
    def apply(marker):
        real_stat = os.stat
        real_isdir = os.path.isdir

        def fake_stat(path, *a, **k):
            if marker.lower() in str(path).lower():
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(path, *a, **k)

        def fake_isdir(path):
            if marker.lower() in str(path).lower():
                return False
            return real_isdir(path)

        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(os.path, "isdir", fake_isdir)
    return apply


class TestLooseResolution:

    def test_finds_a_nested_file_stat_denies(self, tmp_path, blind_stat):
        """The walk used is_dir()/exists()/is_file() at every step, so a
        mod-supplied mesh was rejected three ways: the walk gave up on the
        first virtual directory, the fast path missed, and the closing
        is_file() denied the entry the scan had just matched by name."""
        nif = (tmp_path / "meshes" / "actors" / "character"
               / "FaceCustomization" / "FurryFallout.esp" / "0001.nif")
        nif.parent.mkdir(parents=True)
        nif.write_bytes(b"nif")

        blind_stat("FaceCustomization")
        assert not nif.exists(), "precondition: stat must be blind"

        resolver = AssetResolver(tmp_path, bsa_readers=[])
        found = resolver.resolve(
            r"meshes\actors\character\FaceCustomization\FurryFallout.esp\0001.nif")
        assert found is not None
        assert found.read_bytes() == b"nif"

    def test_case_insensitive_walk_stat_denies(self, tmp_path, blind_stat):
        """Why the walk enumerates at all: on-disk case often differs from
        the path recorded in the plugin."""
        nif = tmp_path / "Meshes" / "Actors" / "Head.nif"
        nif.parent.mkdir(parents=True)
        nif.write_bytes(b"nif")

        blind_stat("Actors")
        resolver = AssetResolver(tmp_path, bsa_readers=[])
        assert resolver.resolve(r"meshes\actors\head.nif") is not None

    def test_missing_file_still_returns_none(self, tmp_path):
        resolver = AssetResolver(tmp_path, bsa_readers=[])
        assert resolver.resolve(r"meshes\nope\absent.nif") is None

    def test_fallback_root_still_searched(self, tmp_path, blind_stat):
        """FO4's resolver searches data_dir then an optional fallback. The
        rewrite must not lose the second root."""
        data = tmp_path / "data"
        fallback = tmp_path / "fallback"
        (fallback / "meshes").mkdir(parents=True)
        (data).mkdir()
        (fallback / "meshes" / "shared.nif").write_bytes(b"nif")

        blind_stat("meshes")
        resolver = AssetResolver(data, bsa_readers=[], fallback_dir=fallback)
        assert resolver.resolve(r"meshes\shared.nif") is not None


class TestArchiveScan:

    def test_opens_archives_when_stat_denies_the_root(self, tmp_path,
                                                      blind_stat, caplog):
        """The archive scan bailed on `if not root.is_dir()`, so under MO2
        no BA2 or BSA was opened at all.

        The data dir gets a distinctive name so the marker really matches:
        an earlier version of this test used "tmp", which is not a
        substring of "Temp", so stat was never blinded and the test passed
        with or without the fix.
        """
        data = tmp_path / "VirtualData"
        data.mkdir()
        (data / "Junk.ba2").write_bytes(b"not really an archive")

        blind_stat("VirtualData")
        assert not data.is_dir(), "precondition: stat must be blind"

        with caplog.at_level(logging.WARNING):
            resolver = AssetResolver.for_data_dir(data)
        try:
            # Reaching the open attempt is the point — before the fix the
            # loop never entered, so nothing was logged at all.
            assert "Junk.ba2" in caplog.text
        finally:
            resolver.close()


class TestBakeIsNotDiscardedByItsOwnLogLine:

    def test_no_stat_call_in_the_write_path(self):
        """`log.debug("... %d bytes", os.path.getsize(p))` evaluates getsize
        whether or not debug is enabled. Under MO2 that stat fails for the
        nif PyNifly just wrote, so on Skyrim this exact line reported
        "0 succeeded, 4685 failed" for a run where every bake had worked.

        Tokenized rather than grepped, so the comment explaining the bug
        doesn't trip the test that guards against it.
        """
        import io
        import inspect
        import tokenize
        from furrifier_fo4.facegen import assemble

        src = inspect.getsource(assemble)
        code = "".join(
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.STRING))
        for banned in ("os.path.getsize", ".stat()"):
            assert banned not in code, (
                f"{banned} in the facegen write path will discard completed "
                "bakes under MO2 - use esplib.utils.file_size")
