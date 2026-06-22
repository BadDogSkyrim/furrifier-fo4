"""Pack a run's loose facegen output into game-loadable BA2 archives.

A full run emits thousands of tiny loose facegen files. FO4 auto-loads
``<plugin-stem> - Main.ba2`` (GNRL) and ``<plugin-stem> - Textures.ba2`` (DX10)
for an enabled plugin, and the patch's archives load AFTER its masters', so the
faces we pack override vanilla's — exactly how facegen/NPC-overhaul mods ship.
Packing means a clean install with no loose clutter for mod managers.

The in-archive paths keep the loose layout (``Meshes\\...\\FaceGeom\\<master>\\
<fid>.nif``) so every base-master subfolder coexists in one archive and the
engine resolves each file by its in-archive path across the global archive VFS.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from esplib import Ba2Writer

log = logging.getLogger(__name__)

# The two facegen output subtrees, relative to the run's output root. These
# mirror facegen/__init__.py's _FACEGEOM_DIR / _FACECUST_DIR.
_FACEGEOM_REL = ("meshes", "Actors", "Character", "FaceGenData", "FaceGeom")
_FACECUST_REL = ("textures", "Actors", "Character", "FaceCustomization")


def pack_facegen(out_root, patch_name) -> list:
    """Pack the run's loose facegen under `out_root` into a pair of BA2s named
    after `patch_name`'s stem, then delete the loose trees that were packed.

    Returns the list of archive Paths written (0, 1, or 2 — an empty subtree is
    skipped, neither archived nor deleted). Loose facegen overrides archives in
    FO4, so leaving it would shadow the BA2; we remove only what we packed."""
    out_root = Path(out_root)
    stem = Path(patch_name).stem
    written: list = []

    geom_dir = out_root.joinpath(*_FACEGEOM_REL)
    nifs = sorted(geom_dir.rglob("*.nif")) if geom_dir.is_dir() else []
    if nifs:
        w = Ba2Writer("GNRL")
        for nif in nifs:
            w.add_file(str(nif.relative_to(out_root)), nif.read_bytes())
        main_path = out_root / f"{stem} - Main.ba2"
        w.write(main_path)
        written.append(main_path)

    tex_dir = out_root.joinpath(*_FACECUST_REL)
    ddses = sorted(tex_dir.rglob("*.dds")) if tex_dir.is_dir() else []
    if ddses:
        w = Ba2Writer("DX10")
        for dds in ddses:
            w.add_dds(str(dds.relative_to(out_root)), dds.read_bytes())
        tex_path = out_root / f"{stem} - Textures.ba2"
        w.write(tex_path)
        written.append(tex_path)

    # Remove the packed loose trees (loose shadows the archive otherwise).
    if nifs:
        shutil.rmtree(geom_dir)
    if ddses:
        shutil.rmtree(tex_dir)

    log.info("packed facegen: %d nifs + %d textures into %d archive(s): %s",
             len(nifs), len(ddses), len(written),
             ", ".join(p.name for p in written) or "(none)")
    return written
