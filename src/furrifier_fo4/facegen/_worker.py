"""Worker process for parallel FO4 facegen baking.

The parent does the cheap serial work (record/path resolution against the
plugin set — `_resolve_info` in `__init__.py`) and ships a list of `_WorkItem`s
to a `ProcessPoolExecutor`. Each worker holds one long-lived `AssetResolver`
for its lifetime — its decoded-mask `image_cache` and BA2-extract cache
amortise across NPCs the same way the serial path does.

Workers do NOT touch the plugin set. Everything they need is already inside the
`info` dict — resolved texture paths, tint layers, head-part entries — so the
plugin set (open BA2 handles, not cleanly pickleable) never crosses the process
boundary. Mirrors the Skyrim furrifier's `facegen/_worker.py`.
"""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .assemble import build_facegen_nif
from .assets import AssetResolver
from .composite import build_facecustomization_dds, build_facecustomization_png

log = logging.getLogger("furrifier_fo4.facegen.worker")


@dataclass
class _WorkItem:
    """One NPC's complete facegen job, fully serializable. `info` is the dict
    built by the parent's `_resolve_info` — resolved base-head textures, tint
    layers, head-part entries, output paths, and flags."""
    edid: str
    info: Dict[str, Any]


@dataclass
class _Result:
    """One NPC's outcome, accumulated by the parent into the stats dict."""
    edid: str
    baked: int
    aux: int
    nif: int
    nif_failed: int
    skipped: int
    error: Optional[str] = None


def _copy_aux(form_id: str, base: dict, resolver, out_dir: Path) -> int:
    """Copy the base head normal/specular to <fid>_msn / <fid>_s. Straight byte
    copy — FFO doesn't tint these, so no recompositing."""
    written = 0
    for rel, suffix in ((base.get("normal"), "_msn"),
                        (base.get("specular"), "_s")):
        if not rel:
            continue
        src = resolver.resolve(rel)
        if src is None:
            log.debug("base %s map not found: %s", suffix, rel)
            continue
        shutil.copyfile(src, out_dir / f"{form_id}{suffix}.dds")
        written += 1
    return written


def bake_from_info(info: Dict[str, Any], resolver) -> _Result:
    """Bake one NPC's FaceCustomization textures (+ aux) and facegeom nif from a
    fully-resolved `info` dict, using `resolver` for asset I/O. Shared by the
    serial path and the worker. Per-NPC exceptions are captured into the result
    (the texture and nif stages fail independently), never propagated."""
    edid = info.get("edid") or info["form_id"]
    form_id = info["form_id"]
    out_dir = Path(info["out_dir"])
    base = info["base"]
    baked = aux = nif = nif_failed = 0

    try:
        build_facecustomization_dds(form_id, base["diffuse"], info["layers"],
                                    resolver, out_dir,
                                    output_size=info["output_size"])
        if info["make_png"]:
            build_facecustomization_png(form_id, base["diffuse"], info["layers"],
                                        resolver, out_dir,
                                        output_size=info["output_size"])
        if info["bake_aux"]:
            aux = _copy_aux(form_id, base, resolver, out_dir)
        baked = 1
    except Exception as exc:
        log.warning("facegen failed for %s (%s): %s", edid, form_id, exc)
        return _Result(edid, 0, 0, 0, 0, skipped=1, error=str(exc))

    if info["bake_nif"]:
        try:
            ok = build_facegen_nif(form_id, info["plugin"], info["headparts"],
                                   resolver, Path(info["nif_path"]),
                                   hair_palette_scale=info["hair_palette_scale"],
                                   base_normal=base.get("normal"),
                                   base_specular=base.get("specular"),
                                   aux_textures=info["bake_aux"])
            nif = 1 if ok else 0
            nif_failed = 0 if ok else 1
        except Exception as exc:
            log.warning("facegeom nif failed for %s: %s", edid, exc)
            nif_failed = 1

    return _Result(edid, baked, aux, nif, nif_failed, skipped=0)


# -- worker-process state ----------------------------------------------------
# Set by `_worker_init`, read by `_bake_one`. Each spawned worker has its own
# copy (spawn isolation), so this is effectively a per-process singleton.
_resolver: Optional[AssetResolver] = None


def _set_below_normal_priority() -> None:
    """Demote this process to BELOW_NORMAL on Windows so the user's foreground
    apps preempt freely. No-op off Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(),
                                  BELOW_NORMAL_PRIORITY_CLASS)
    except Exception as exc:
        log.debug("could not set BELOW_NORMAL priority: %s", exc)


def _install_queue_logging(log_queue: Any, level: int) -> None:
    """Route the worker's root logger onto `log_queue` (the parent listens via a
    QueueListener), replacing pynifly's import-time stderr handler so logs don't
    double-emit or vanish in the frozen GUI build."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(level)
    if level > logging.DEBUG:
        logging.getLogger("pynifly").setLevel(logging.ERROR)
        logging.getLogger("esplib").setLevel(logging.WARNING)


def _close_resolver() -> None:
    global _resolver
    if _resolver is not None:
        try:
            _resolver.close()
        except Exception:
            pass
        _resolver = None


def _worker_init(data_dir_str: str, throttle: bool,
                 log_queue: Optional[Any] = None,
                 log_level: int = logging.INFO) -> None:
    """Per-worker initializer (run once per process by ProcessPoolExecutor):
    set priority, wire logging, open BA2s once into a long-lived resolver."""
    global _resolver
    if throttle:
        _set_below_normal_priority()
    if log_queue is not None:
        _install_queue_logging(log_queue, log_level)
    _resolver = AssetResolver.for_data_dir(Path(data_dir_str))
    atexit.register(_close_resolver)


def _bake_one(item: _WorkItem) -> _Result:
    if _resolver is None:
        return _Result(item.edid, 0, 0, 0, 0, skipped=1,
                       error="resolver not initialized")
    return bake_from_info(item.info, _resolver)


def _install_resolver_for_testing(resolver) -> None:
    """Install a pre-built resolver so in-process tests can call `_bake_one`
    without spawning the pool."""
    global _resolver
    _resolver = resolver


def pick_worker_count(throttle: bool,
                      env_override: Optional[str] = None) -> int:
    """Workers to spawn: explicit env (FURRIFY_FO4_FACEGEN_WORKERS) → throttle=1
    → min(16, cpu-1). Subtract one core so the GUI/progress stays responsive."""
    env_val = (env_override if env_override is not None
               else os.environ.get("FURRIFY_FO4_FACEGEN_WORKERS"))
    if env_val:
        try:
            n = int(env_val)
            if n >= 1:
                return min(n, os.cpu_count() or n)
        except ValueError:
            log.warning("FURRIFY_FO4_FACEGEN_WORKERS=%r not an int; ignoring",
                        env_val)
    if throttle:
        return 1
    return max(1, min(16, (os.cpu_count() or 4) - 1))
