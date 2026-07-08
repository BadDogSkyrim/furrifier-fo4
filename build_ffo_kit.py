"""Build the Furry Fallout release kits from active Vortex mods.

Analogous to Skyrim's ``Build_YAS_Reborn.bat``, but does more: it packs loose
assets into BA2s (via esplib's ``Ba2Writer``), copies ESPs / loose sets into the
FOMOD's functional folders, bundles the furrifier, and archives three kits:

    SFW    -> Furry_Fallout.7z         (the main FOMOD)
    NSFW   -> Furry_Fallout_NSFW.7z    (separate FOMOD, loose assets)
    facegen-> Furry_Fallout_Facegen.zip (prebuilt faces, no FOMOD)

The FOMOD's ``Fomod\\ModuleConfig.xml`` + ``Images\\`` are STATIC — authored once
to match the folder layout below — so this script never rewrites them; it only
refreshes the packed/copied payload the XML installs.

See ``PLAN_FFO_KIT_BUILD.md`` for the full source->destination mapping.

Usage (from any dir; needs esplib importable — the repo's editable install):

    python build_ffo_kit.py [sfw|nsfw|facegen|all]  [--dry-run] [--no-archive]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from esplib import Ba2Writer

log = logging.getLogger("build_ffo")

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
MODS = Path(r"C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods")
KITS = Path(r"C:\Users\hughr\OneDrive\Fallout4Dev\KitsFFO")
KIT_SFW = KITS / "Furry_Fallout"
KIT_NSFW = KITS / "Furry_Fallout_NSFW"
DIST = Path(r"C:\Modding\xEditDev\furrifier_fo4\dist\furrify_fo4")

# Authored FOMOD configs live in the repo (version-controlled) and are copied
# into each kit's Fomod\ so the shipped XML always tracks the folder layout this
# script produces. Images\ + info.xml stay static in the kit.
REPO_FOMOD = Path(__file__).resolve().parent / "fomod"

# Final release archives land in KitsFFO, alongside the kit source folders.
ARCHIVE_SFW = KITS / "Furry_Fallout.7z"
ARCHIVE_NSFW = KITS / "Furry_Fallout_NSFW.7z"
ARCHIVE_FACEGEN = KITS / "Furry_Fallout_Facegen.zip"

# Extensions that never belong inside a BA2 (plugins / other archives / junk).
_PLUGIN_EXTS = {".esp", ".esm", ".esl"}
_JUNK_EXTS = {".log", ".bak"}
_SKIP_IN_BA2 = _PLUGIN_EXTS | _JUNK_EXTS | {".ba2"}

# Archive2 (ships with the Creation Kit). Used only for the DX10 texture archive
# when a source contains a DDS esplib's Ba2Writer can't model (uncompressed /
# cubemap). esplib handles the other ~99.8% and is the fast in-process path.
_ARCHIVE2_CANDIDATES = [
    Path(r"C:\Steam\steamapps\common\Fallout 4\Tools\Archive2\Archive2.exe"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\Fallout 4\Tools\Archive2\Archive2.exe"),
]
# esplib's add_dds understands these fourCCs; a zero fourCC (uncompressed) or a
# cubemap (caps2 flag) it does NOT — those force the Archive2 path.
_ESPLIB_FOURCCS = {b"DXT1", b"DXT3", b"DXT5", b"ATI2", b"BC5U", b"DX10"}
_DDSCAPS2_CUBEMAP = 0x200

# Module-level switch so every filesystem mutation can be traced without doing
# it. Set by main() from --dry-run.
DRY_RUN = False


# --------------------------------------------------------------------------
# Exclusion rules  (xxx*, *TEST* on either end, junk extensions)
# --------------------------------------------------------------------------
def is_excluded(name: str) -> bool:
    """True if a file/folder name should never be shipped."""
    low = name.lower()
    if low.startswith("xxx"):
        return True
    base = name.rsplit(".", 1)[0]
    if base.upper().startswith("TEST") or base.upper().endswith("TEST"):
        return True
    if Path(name).suffix.lower() in _JUNK_EXTS:
        return True
    return False


# --------------------------------------------------------------------------
# Filesystem helpers (dry-run aware)
# --------------------------------------------------------------------------
def _on_rm_error(func, path, exc) -> None:
    """rmtree onexc: clear a read-only bit and retry the failing op once."""
    try:
        os.chmod(path, 0o666)
        func(path)
    except OSError:
        raise


def _rm(path: Path) -> None:
    """Remove a file or directory tree, tolerating the transient 'Access is
    denied' locks OneDrive throws while it syncs a folder (the kit lives under
    OneDrive). Retries a few times with a short backoff."""
    for attempt in range(6):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, onexc=_on_rm_error)
            else:
                path.unlink(missing_ok=True)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.5)


def _clean(dest: Path) -> None:
    """Empty `dest` but KEEP the directory itself. We deliberately don't rmtree
    `dest`: it's a OneDrive-synced folder, and os.rmdir on the synced top dir
    intermittently fails with WinError 5 even after its contents are gone.
    Clearing the contents is equivalent for a full rebuild and sidesteps that."""
    if dest.exists():
        log.info("  clean  %s", dest)
        if not DRY_RUN:
            for child in sorted(dest.iterdir()):
                _rm(child)


def _mkdir(dest: Path) -> None:
    if not DRY_RUN:
        dest.mkdir(parents=True, exist_ok=True)


def copy_file(src: Path, dest_dir: Path) -> None:
    """Copy one file into dest_dir (created if needed)."""
    if not src.exists():
        log.warning("  MISSING file: %s", src)
        return
    _mkdir(dest_dir)
    log.info("  copy   %s -> %s\\", src.name, dest_dir)
    if not DRY_RUN:
        shutil.copy2(src, dest_dir / src.name)


def copy_tree(src: Path, dest: Path) -> int:
    """Copy a whole tree into dest, honoring is_excluded on every path
    component. Returns the number of files copied."""
    if not src.is_dir():
        log.warning("  MISSING dir: %s", src)
        return 0
    n = 0
    for f in src.rglob("*"):
        if f.is_dir():
            continue
        rel = f.relative_to(src)
        if any(is_excluded(part) for part in rel.parts):
            continue
        target = dest / rel
        if not DRY_RUN:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        n += 1
    log.info("  copy   %s\\  (%d files) -> %s\\", src.name, n, dest)
    return n


# --------------------------------------------------------------------------
# Packing:  loose folders -> "<stem> - Main.ba2" (GNRL) + "- Textures.ba2" (DX10)
# --------------------------------------------------------------------------
def _archive2_exe() -> Path | None:
    for c in _ARCHIVE2_CANDIDATES:
        if c.is_file():
            return c
    return None


def _dds_esplib_packable(path: Path) -> bool:
    """True if esplib's Ba2Writer can model this .dds. It handles the common
    block-compressed fourCCs and DX10-header DDS, but NOT uncompressed (zero
    fourCC) or cubemaps (it never sets the isCubemap header byte)."""
    try:
        with open(path, "rb") as fh:
            h = fh.read(128)
    except OSError:
        return False
    if h[:4] != b"DDS ":
        return False
    caps2 = struct.unpack_from("<I", h, 112)[0]
    if caps2 & _DDSCAPS2_CUBEMAP:
        return False
    return h[84:88] in _ESPLIB_FOURCCS


def _pack_textures_archive2(dds: list[tuple[Path, Path]], tex_path: Path) -> None:
    """Pack (abs, rel) DDS into `tex_path` with Archive2. Hardlink-stage the
    files under one temp root (rel paths preserved) so multi-source packs merge
    into a single archive namespace and Archive2 gets one clean -root."""
    a2 = _archive2_exe()
    if a2 is None:
        raise RuntimeError("Archive2.exe not found; needed for uncompressed/"
                           "cubemap textures. Install the Creation Kit.")
    with tempfile.TemporaryDirectory(prefix="ffo_tex_") as td:
        root = Path(td)
        for abs_path, rel in dds:
            staged = root / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(abs_path, staged)      # near-free on the same volume
            except OSError:
                shutil.copy2(abs_path, staged)  # cross-volume fallback
        args = [str(a2), str(root), "-create=" + str(tex_path),
                "-format=DDS", "-root=" + str(root),
                "-compression=Default", "-quiet"]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Archive2 failed ({r.returncode}): {r.stderr.strip()}")


def pack(sources: list[Path], dest_dir: Path, stem: str) -> None:
    """Pack every non-plugin file under each source folder into a BA2 pair named
    after `stem`, written into `dest_dir`. `.dds` -> DX10 texture archive;
    everything else (meshes, materials, ...) -> GNRL main archive. In-archive
    paths are relative to each source root, so multiple sources merge into one
    namespace (e.g. World_Working + posters + stuff).

    Textures pack with esplib when every DDS is esplib-packable; if any source
    holds an uncompressed/cubemap DDS, the whole texture archive is built with
    Archive2 instead (guaranteed-correct, matches the vanilla pipeline)."""
    gnrl = Ba2Writer("GNRL")
    dds: list[tuple[Path, Path]] = []   # (abs, rel) texture files
    n_main = n_skip = 0

    for src in sources:
        if not src.is_dir():
            log.warning("  MISSING pack source: %s", src)
            continue
        for f in sorted(src.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(src)
            if any(is_excluded(part) for part in rel.parts):
                continue
            ext = f.suffix.lower()
            if ext in _SKIP_IN_BA2:
                n_skip += 1
            elif ext == ".dds":
                dds.append((f, rel))
            else:
                gnrl.add_file(str(rel), f.read_bytes())
                n_main += 1

    use_a2 = any(not _dds_esplib_packable(f) for f, _ in dds)
    _mkdir(dest_dir)
    main_path = dest_dir / f"{stem} - Main.ba2"
    tex_path = dest_dir / f"{stem} - Textures.ba2"
    log.info("  pack   %d files -> %s  |  %d dds -> %s [%s]  (%d skipped)",
             n_main, main_path.name, len(dds), tex_path.name,
             "Archive2" if use_a2 else "esplib", n_skip)
    if DRY_RUN:
        return
    if len(gnrl):
        gnrl.write(main_path)
    if dds:
        if use_a2:
            _pack_textures_archive2(dds, tex_path)
        else:
            dx10 = Ba2Writer("DX10")
            for f, rel in dds:
                dx10.add_dds(str(rel), f.read_bytes())
            dx10.write(tex_path)


def copy_moduleconfig(kit_name: str, kit_dir: Path) -> None:
    """Copy the repo's authored ModuleConfig.xml for this kit into kit\\Fomod\\.
    Images\\ + info.xml remain static in the kit."""
    src = REPO_FOMOD / kit_name / "ModuleConfig.xml"
    if not src.is_file():
        log.warning("  MISSING ModuleConfig: %s", src)
        return
    copy_file(src, kit_dir / "Fomod")


def copy_esps(src: Path, dest_dir: Path, names=None) -> None:
    """Copy plugins from src into dest_dir. If names is None, copy every
    non-excluded *.esp/*.esm/*.esl at the source root."""
    if not src.is_dir():
        log.warning("  MISSING esp source: %s", src)
        return
    if names is None:
        plugins = [f for f in src.iterdir()
                   if f.suffix.lower() in _PLUGIN_EXTS and not is_excluded(f.name)]
    else:
        plugins = [src / n for n in names]
    for p in sorted(plugins):
        copy_file(p, dest_dir)


# --------------------------------------------------------------------------
# Kit builders
# --------------------------------------------------------------------------
def build_sfw() -> None:
    log.info("=== SFW kit -> %s ===", KIT_SFW)

    # 1. Primary assets (base + DLC) -> Data\ BA2 pair, plus all ESPs.
    _clean(KIT_SFW / "Data")
    pack([MODS / "Furry Fallout Assets"], KIT_SFW / "Data", "FurryFallout")
    copy_esps(MODS / "Furry Fallout Assets", KIT_SFW / "Data")  # main + DLC + 10 players

    # 2. Furry World: pack working world + posters + stuff; keep the esp.
    _clean(KIT_SFW / "World")
    pack([MODS / "Furry_Fallout_World_Working",
          MODS / "More_Furry_Posters (1)",
          MODS / "More_Furry_Stuff"], KIT_SFW / "World", "FurryFalloutWorld")
    copy_esps(MODS / "Furry_Fallout_World_Working", KIT_SFW / "World",
              ["FurryFalloutWorld.esp"])

    # 3. Furry World DLC: pack DLC working world; keep the esp.
    _clean(KIT_SFW / "WorldDLC")
    pack([MODS / "Furry_Fallout_DLC_World_Working"], KIT_SFW / "WorldDLC",
         "FurryFalloutWorldDLC")
    copy_esps(MODS / "Furry_Fallout_DLC_World_Working", KIT_SFW / "WorldDLC",
              ["FurryFalloutWorldDLC.esp"])

    # 4. Pawfeet outfits (loose): plugin + prebuilt outfit meshes.
    _clean(KIT_SFW / "Pawfeet")
    copy_esps(MODS / "Furry Fallout Prebuilt Pawfeet", KIT_SFW / "Pawfeet",
              ["FurryFalloutOutfits.esp"])
    copy_tree(MODS / "Furry Fallout Prebuilt Pawfeet" / "Meshes",
              KIT_SFW / "Pawfeet" / "Meshes")

    # 5. Tools: the furrifier tool. (Prebuilt Bodies + BodySlide moved to the
    #    NSFW kit — some bodies are nude, and bodyslide work implies nudity.)
    _clean(KIT_SFW / "Tools")
    copy_tree(DIST, KIT_SFW / "Tools" / "Furrifier")
    copy_file(REPO_FOMOD.parent / "FURRIFIER_HOWTO.md", KIT_SFW / "Tools" / "Furrifier")

    # 7. Patches (own FOMOD tab; conditional installs keyed on the target esp).
    _clean(KIT_SFW / "Patches")
    #   EAC: pack assets, ship the (non-TEST) plugin.
    pack([MODS / "FFO_EAC_Patch"], KIT_SFW / "Patches" / "EAC", "FFO_EAC_Patch")
    copy_esps(MODS / "FFO_EAC_Patch", KIT_SFW / "Patches" / "EAC",
              ["FFO_EAC_Patch.esp"])
    #   WAT: loose assets only (no esp).
    copy_tree(MODS / "FFO WAT Patch Assets" / "Meshes",
              KIT_SFW / "Patches" / "WAT" / "Meshes")
    #   SS2: already packed; ship esps + ba2 as-is.
    copy_tree(MODS / "Furry_Fallout_SS2 (3.7)", KIT_SFW / "Patches" / "SS2")

    # ModuleConfig from the repo; Images\ + info.xml stay static in the kit.
    copy_moduleconfig("sfw", KIT_SFW)


def build_nsfw() -> None:
    """Assemble the NSFW addon FOMOD (functional folders, mirroring build_sfw).

    Per the plan's authoritative mapping. `Fomod\\` + `images\\` are HAND-AUTHORED
    and STATIC — this never touches them (that's the fix for the earlier clobber).
    Only the content folders are regenerated from their Vortex sources."""
    log.info("=== NSFW kit -> %s ===", KIT_NSFW)

    # bodies: prebuilt nude bodies (loose). Some are nude -> this is why bodies
    # and bodyslide live in the NSFW kit, not SFW.
    _clean(KIT_NSFW / "bodies")
    copy_tree(MODS / "Furry Fallout Prebuilt Bodies" / "Meshes",
              KIT_NSFW / "bodies" / "Meshes")

    # world: the packable NSFW content. Copy both plugins (FFO_NSFW.esp and
    # FFO_NSFW_LongJohns.esp — the ModuleConfig splits them across the Furry
    # World / Long Johns options), pack the loose assets into FFO_NSFW BA2s.
    _clean(KIT_NSFW / "world")
    pack([MODS / "Furry Fallout NSFW Packable Assets"], KIT_NSFW / "world", "FFO_NSFW")
    copy_esps(MODS / "Furry Fallout NSFW Packable Assets", KIT_NSFW / "world",
              ["FFO_NSFW.esp", "FFO_NSFW_LongJohns.esp"])

    # loose: AAF configs + meshes shipped loose (AAF needs them loose).
    _clean(KIT_NSFW / "loose")
    nsfw_assets = MODS / "Furry Fallout NSFW Assets"
    copy_tree(nsfw_assets / "AAF", KIT_NSFW / "loose" / "AAF")
    copy_tree(nsfw_assets / "Meshes", KIT_NSFW / "loose" / "Meshes")

    # femmags: female porn-magazine textures (loose).
    _clean(KIT_NSFW / "femmags")
    copy_tree(MODS / "Furry Fallout Female Magazines" / "textures",
              KIT_NSFW / "femmags" / "textures")

    # Bodyslide: shapes for the furry/nude bodies (excl. XXXTools via is_excluded).
    _clean(KIT_NSFW / "Bodyslide")
    copy_tree(MODS / "Furry Fallout Bodyslide" / "Tools",
              KIT_NSFW / "Bodyslide" / "Tools")

    # Fomod\ + images\ are hand-authored + static — deliberately NOT touched.
    log.info("  (NSFW Fomod\\ + images\\ left untouched - hand-authored)")


def build_facegen_zip() -> None:
    """The facegen kit is a plain archive of the already-packed FFO Working
    Facegen (esp + Main.ba2 + Textures.ba2), no FOMOD."""
    log.info("=== Facegen kit -> %s ===", ARCHIVE_FACEGEN)
    src = MODS / "FFO Working Facegen"
    if not src.is_dir():
        log.warning("  MISSING facegen source: %s", src)
        return
    names = [f.name for f in sorted(src.iterdir())
             if f.is_file() and not is_excluded(f.name)]
    log.info("  facegen files: %s", ", ".join(names))
    _archive(ARCHIVE_FACEGEN, src, kind="zip", names=names)


# --------------------------------------------------------------------------
# Archiving  (7-Zip)
# --------------------------------------------------------------------------
def _hydrate(folder: Path) -> None:
    """Force OneDrive to download any cloud-only ('Files On-Demand') files under
    `folder` before we archive it. 7z reads dehydrated files inline, and a slow
    cloud fetch makes it fail with a time-out (bit us on Images\\*.png). We touch
    only files flagged OFFLINE / RECALL_ON_DATA_ACCESS, so freshly-written local
    BA2s aren't re-read."""
    OFFLINE, RECALL = 0x1000, 0x400000
    n = 0
    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        try:
            attrs = f.stat().st_file_attributes  # Windows-only
        except (OSError, AttributeError):
            continue
        if attrs & (OFFLINE | RECALL):
            try:
                with open(f, "rb") as fh:
                    fh.read(1)          # access triggers the download
                n += 1
            except OSError as e:
                log.warning("  hydrate failed: %s (%s)", f, e)
    if n:
        log.info("  hydrate %d cloud-only file(s) in %s", n, folder.name)


def _archive(out_path: Path, folder: Path, kind: str = "7z", names=None) -> None:
    """Archive into out_path. If `names` is given, only those basenames from
    `folder` are added (each at the archive root — 7z runs with cwd=folder so no
    absolute paths leak in); otherwise the folder's whole contents are added."""
    if out_path.exists():
        log.info("  del    existing %s", out_path.name)
        if not DRY_RUN:
            out_path.unlink()
    if not DRY_RUN:
        _hydrate(folder)
    fmt = "-t7z" if kind == "7z" else "-tzip"
    items = names if names else ["*"]
    args = ["7z", "a", fmt, str(out_path), *items, "-mx=9"]
    log.info("  archive %s  (cwd=%s)", out_path.name, folder)
    if DRY_RUN:
        log.info("    (dry-run) %s", " ".join(args))
        return
    r = subprocess.run(args, cwd=str(folder), capture_output=True, text=True)
    if r.returncode != 0:
        log.error("  7z failed (%d): %s", r.returncode, r.stderr.strip())
    else:
        log.info("  OK     %s  (%s)", out_path.name,
                 f"{out_path.stat().st_size:,} b" if out_path.exists() else "?")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main() -> int:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Build the Furry Fallout kits.")
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["sfw", "nsfw", "facegen", "all"])
    ap.add_argument("--dry-run", action="store_true",
                    help="log every action without touching the filesystem")
    ap.add_argument("--no-archive", action="store_true",
                    help="assemble the kit folders but skip the .7z/.zip step")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    start = time.perf_counter()
    log.info("Building FFO kit(s): mode=%s  dry_run=%s", args.mode, DRY_RUN)

    do_sfw = args.mode in ("sfw", "all")
    do_nsfw = args.mode in ("nsfw", "all")
    do_facegen = args.mode in ("facegen", "all")

    if do_sfw:
        build_sfw()
        if not args.no_archive:
            _archive(ARCHIVE_SFW, KIT_SFW, "7z")
    if do_nsfw:
        build_nsfw()
        if not args.no_archive:
            _archive(ARCHIVE_NSFW, KIT_NSFW, "7z")
    if do_facegen:
        build_facegen_zip()  # this one archives directly

    log.info("Done in %.1fs", time.perf_counter() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
