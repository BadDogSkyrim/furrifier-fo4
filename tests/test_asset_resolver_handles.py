"""AssetResolver.release_handles: after a run the resolver must drop its open
archive (BA2) file handles so a mod manager can deploy while the GUI stays
alive, then transparently reopen them on the next archive read.

Pure test: builds a real one-file-per-entry BA2 with esplib's Ba2Writer, no game
install needed.
"""

from __future__ import annotations

import os
from pathlib import Path

from esplib.ba2 import Ba2Writer

from furrifier_fo4.facegen.assets import AssetResolver


def _make_ba2(path: Path, entries: dict[str, bytes]) -> None:
    w = Ba2Writer("GNRL")
    for rel, data in entries.items():
        w.add_file(rel, data)
    w.write(path)


def _make_data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "Data"
    data.mkdir()
    _make_ba2(data / "Test - Main.ba2", {
        r"meshes\test\a.nif": b"AAAA",
        r"meshes\test\b.nif": b"BBBB",
    })
    return data


def test_release_frees_the_archive_lock_and_reopens(tmp_path):
    data = _make_data_dir(tmp_path)
    resolver = AssetResolver.for_data_dir(data)

    # A read extracts from the archive (proves the reader is open + working).
    a = resolver.resolve(r"meshes\test\a.nif")
    assert a is not None and a.read_bytes() == b"AAAA"

    # Release: handles closed, reader list emptied, flagged for reopen.
    resolver.release_handles()
    assert resolver._bsa_readers == []
    assert resolver._readers_released is True

    # The archive is now unlocked — a mod manager could relink/replace it.
    # (On Windows an unreleased handle would make this raise PermissionError.)
    ba2 = data / "Test - Main.ba2"
    moved = data / "Test - Main.ba2.moved"
    os.replace(ba2, moved)
    os.replace(moved, ba2)

    # A subsequent read for an entry NOT already cached reopens the archive.
    b = resolver.resolve(r"meshes\test\b.nif")
    assert b is not None and b.read_bytes() == b"BBBB"
    assert resolver._bsa_readers          # reopened
    assert resolver._readers_released is False

    resolver.close()


def test_release_is_idempotent_and_safe_without_roots(tmp_path):
    # A loose-only resolver (explicit empty readers, no scan roots) tolerates
    # release_handles without error and never tries to rescan.
    resolver = AssetResolver(tmp_path, bsa_readers=[])
    resolver.release_handles()
    resolver.release_handles()
    assert resolver._bsa_readers == []
