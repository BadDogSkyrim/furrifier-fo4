"""Asset resolver: map Data-relative paths to concrete on-disk paths.

The facegen engine reads source headpart nifs, chargen tri files, and
tint masks by their canonical Data-relative paths (e.g.
`meshes\\actors\\character\\MaleHead.nif`). In the test fixture tree
those are always loose files; for live furrifier runs against a real
install they live inside `Skyrim - Meshes0.bsa` / `Skyrim - Textures.bsa`
and a handful of other archives.

AssetResolver tries loose first, then falls back to scanning every BSA
in the Data directory. BSA-sourced files are extracted once into a
per-run temp directory and the cached path handed to callers, so
PyNifly / PIL can open them by path without changes.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional


log = logging.getLogger("furrifier.facegen.assets")


class AssetResolver:
    """Resolve a Data-relative asset path to a concrete file on disk.

    Loose files under `data_dir` win over BSA content, matching the
    game's own precedence rules.

    Typical live use:
        with AssetResolver.for_data_dir(data_dir) as resolver:
            nif_path = resolver.resolve("meshes\\actors\\character\\foo.nif")
            if nif_path is not None:
                nif = NifFile(str(nif_path))

    Tests construct a resolver with an explicit `bsa_readers=[...]` list
    (or an empty list for loose-only scenarios) to avoid depending on a
    real game install.
    """

    def __init__(self, data_dir: Path, bsa_readers: Optional[Iterable] = None,
                 cache_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self._bsa_readers: List = list(bsa_readers) if bsa_readers is not None else []
        # Cache: relpath-key (backslash, lowercase) -> absolute path on disk.
        self._resolved: dict[str, Path] = {}
        # Decoded-image cache piggybacked on the run-scoped resolver.
        # Owned by whoever populates it (currently `composite.py`); the
        # resolver just provides a place to hang it. Many NPCs of the
        # same race share masks, and Pillow's DDS decoder is expensive.
        # Key shape is opaque to the resolver.
        self.image_cache: dict = {}
        # Temp dir for BSA extractions. Lazily created on first extract so
        # loose-only runs don't touch the temp filesystem.
        self._cache_dir: Optional[Path] = (
            Path(cache_dir) if cache_dir is not None else None
        )
        self._owns_cache_dir = cache_dir is None

    # ------------------------------------------------------------ factory --

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "AssetResolver":
        """Scan `data_dir` for *.bsa files, open each, and return a
        resolver wired up with all of them.

        Archives that fail to parse (wrong version, corrupt header,
        non-archive content) are logged and skipped — we don't want one
        broken archive in the Data folder to abort a run.

        FO4 ships assets in both `*.bsa` (rare) and `*.ba2` (the norm),
        so both are scanned; loose files still win over either.
        """
        data_dir = Path(data_dir)
        readers: List = []
        if data_dir.is_dir():
            # Import here so the module-level import graph stays clean
            # for test environments that don't have esplib on sys.path.
            from esplib.bsa import BsaReader, BsaError
            from esplib.ba2 import Ba2Reader

            for candidate in sorted(data_dir.glob("*.bsa")):
                try:
                    reader = BsaReader(candidate)
                    reader.open()
                    readers.append(reader)
                except (BsaError, OSError) as exc:
                    log.warning("skipping %s: %s", candidate.name, exc)
            for candidate in sorted(data_dir.glob("*.ba2")):
                try:
                    reader = Ba2Reader(candidate)
                    reader.open()
                    readers.append(reader)
                except (ValueError, OSError) as exc:
                    log.warning("skipping %s: %s", candidate.name, exc)
        return cls(data_dir, bsa_readers=readers)

    # ------------------------------------------------------------ context --

    def __enter__(self) -> "AssetResolver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Release BSA handles and remove the temp cache dir (if we
        created it)."""
        for reader in self._bsa_readers:
            try:
                reader.close()
            except Exception as exc:
                log.debug("bsa close failed: %s", exc)
        self._bsa_readers = []

        if self._owns_cache_dir and self._cache_dir is not None and self._cache_dir.exists():
            try:
                shutil.rmtree(self._cache_dir, ignore_errors=True)
            except Exception as exc:
                log.debug("cache cleanup failed: %s", exc)
        self._cache_dir = None

    @property
    def cache_root(self) -> Optional[str]:
        """The temp-cache root (where BA2-extracted assets land), or None if
        nothing's been extracted yet. Pass as `NifFile(materialsRoot=...)` so
        PyNifly resolves a BA2 BGSM that `ensure_materials_cached` extracted
        here — even when the nif itself is loose (PyNifly otherwise searches
        only relative to the nif, i.e. the real Data dir, and misses BA2
        materials)."""
        return str(self._cache_dir) if self._cache_dir is not None else None

    # ----------------------------------------------------------- resolve --

    def resolve(self, relpath: str) -> Optional[Path]:
        """Return an absolute path for `relpath`, or None if not found.

        `relpath` is a Data-relative path in Bethesda's convention:
        backslash-separated, typically beginning with `meshes\\` or
        `textures\\`. Case is ignored throughout.
        """
        key = relpath.replace("/", "\\").lower()
        cached = self._resolved.get(key)
        if cached is not None:
            return cached

        loose = self._find_loose(relpath)
        if loose is not None:
            self._resolved[key] = loose
            return loose

        extracted = self._extract_from_bsa(relpath)
        if extracted is not None:
            self._resolved[key] = extracted
            return extracted

        return None

    # ------------------------------------------------------------- loose --

    def _find_loose(self, relpath: str) -> Optional[Path]:
        """Case-insensitive loose-file lookup under data_dir.

        Walk segment-by-segment so we don't depend on the Windows
        filesystem's case handling — callers occasionally hit real-world
        cases where the actual file on disk is `Meshes\\actors\\...`
        with a capital M.
        """
        parts = relpath.replace("/", "\\").split("\\")
        current = self.data_dir
        for segment in parts:
            if not current.is_dir():
                return None
            # Fast path: exact match
            direct = current / segment
            if direct.exists():
                current = direct
                continue
            # Slow path: case-insensitive scan of this directory
            target = segment.lower()
            match = None
            for entry in current.iterdir():
                if entry.name.lower() == target:
                    match = entry
                    break
            if match is None:
                return None
            current = match
        return current if current.is_file() else None

    # --------------------------------------------------------------- bsa --

    def _extract_from_bsa(self, relpath: str) -> Optional[Path]:
        key = relpath.replace("/", "\\")
        for reader in self._bsa_readers:
            if reader.has_file(key):
                data = reader.read_file(key)
                return self._write_cache(relpath, data)
        return None

    def _ensure_cache_dir(self) -> Path:
        if self._cache_dir is None:
            self._cache_dir = Path(tempfile.mkdtemp(prefix="furrifier_facegen_"))
            self._owns_cache_dir = True
        return self._cache_dir

    def _write_cache(self, relpath: str, data: bytes) -> Path:
        cache_dir = self._ensure_cache_dir()
        # Preserve the relative path structure so debugging is sane —
        # the cached file at meshes/actors/character/foo.nif is
        # obviously its loose-path equivalent.
        normalized = relpath.replace("\\", "/")
        out = cache_dir / normalized
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out
