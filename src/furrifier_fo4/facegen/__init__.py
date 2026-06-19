"""FO4 self-baked FaceGen.

For each furrified NPC override in the patch, bake the per-NPC
**FaceCustomization** texture set under
`textures\\Actors\\Character\\FaceCustomization\\<defining-plugin>\\<formid>_*.dds`:

  - `_d`   diffuse: the race's base head diffuse with the NPC's tint layers
           composited in using their real FO4 blend ops (`composite.py`), the
           part the CK flattens to plain alpha-over. This is genuinely per-NPC.

The normal (`_msn`) and specular (`_s`) are RACE-CONSTANT — FFO tints only the
diffuse — so the baked nif's head points its Normal/Specular slots straight at
the shared base-head maps instead of writing an identical per-NPC `_msn`/`_s`
for every NPC of a race. That duplication multiplied face-texture VRAM ~7x
(2048² normal+spec per NPC vs vanilla's 1024²/512²) and exhausted the GPU in
crowds, AV-ing the renderer's shadow/deferred-prepass pass. `bake_aux=True`
restores the old per-NPC copies (for a future pass that bakes layers onto the
normal, e.g. scars); the nif then points back at the per-NPC maps.

FO4 also runtime-generates the head geometry when a facegeom nif is missing,
but doing so for a whole cell of furrified NPCs at once spikes load-time
processing and causes glitches — so we also bake the facegeom nif per NPC
(`assemble.py`) under `meshes\\...\\FaceGeom\\<defining-plugin>\\<formid>.nif`,
moving that cost to build time.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Optional

from esplib import race_height

from ..extract import FactExtractor


def _npc_skin_tone(npc):
    """The NPC's skin-tone RGBA (0-1) from its QNAM subrecord (the furrifier
    writes the skin tone there while tinting). None if absent. CK bakes this
    into a Skin-Tint shape's skinTintColor — e.g. the furry deer horn base."""
    q = npc.get_subrecord("QNAM")
    if q is None or q.size < 16:
        return None
    return struct.unpack("<ffff", q.data[:16])
from ..headparts import HeadpartPools
from ..models import Sex
from .assets import AssetResolver
from .basehead import BaseHeadTextures
from .extract import RaceTintTemplates, npc_tint_layers

log = logging.getLogger(__name__)

_FACECUST_DIR = "textures/Actors/Character/FaceCustomization"
_FACEGEOM_DIR = "meshes/Actors/Character/FaceGenData/FaceGeom"


def base_plugin_for(npc, patch) -> str:
    """Plugin filename that 'owns' the NPC for FaceGenData pathing — the
    plugin that DEFINED the record, not the override that wins.

    The FO4 engine looks for facegen under the base record's plugin, so a
    furrified vanilla NPC's textures must live under `Fallout4.esm\\` (or the
    DLC esm), even though our patch is the winning override. Records the
    furrifier creates itself (file_index past the master list) get the patch
    name.
    """
    idx = npc.form_id.file_index
    masters = patch.header.masters
    if idx < len(masters):
        return masters[idx]
    return patch.file_path.name if patch.file_path else "patch.esp"


def _npc_morphs(npc, race_edid, sex, race_morphs) -> list:
    """Read an NPC's chargen face morphs as [(chargen-tri-morph-name, weight)].

    The NPC's MSDK keys are preset MPPIs; map each to its MPPM (the .tri shape-
    key name) via the RACE. Unmappable keys (e.g. RACE 'Morph Values' slider
    keys, which aren't chargen shape keys) are skipped. Empty without
    `race_morphs` or MSDK/MSDV.
    """
    if race_morphs is None:
        return []
    msdk = npc.get_subrecord("MSDK")
    msdv = npc.get_subrecord("MSDV")
    if msdk is None or msdv is None:
        return []
    n = min(len(msdk.data) // 4, len(msdv.data) // 4)
    keys = struct.unpack_from(f"<{n}I", msdk.data)
    vals = struct.unpack_from(f"<{n}f", msdv.data)
    out = []
    for key, weight in zip(keys, vals):
        mppm = race_morphs.mppm_for(race_edid, sex, key)
        if mppm:
            out.append((mppm, weight))
    return out


def _npc_regions(npc) -> list:
    """[(fmri, (7 floats))] from the NPC's Face Morph (FMRI/FMRS) records — the
    region bone transforms the facebone bake deforms."""
    out = []
    pending = None
    for sr in npc.subrecords:
        s = sr.signature
        if s == "FMRI" and len(sr.data) >= 4:
            pending = int.from_bytes(sr.data[:4], "little")
        elif s == "FMRS" and pending is not None and len(sr.data) >= 28:
            out.append((pending, struct.unpack_from("<7f", sr.data)))
            pending = None
    return out


def _facebone_deltas(npc, race_edid, sex, bone_regions) -> dict:
    """{bare-bone-name: 4x4 delta (list)} for the NPC's region morphs, or {}.
    Each region's FMRI maps (via the FacialBoneRegions JSON) to its bones, whose
    local delta transforms come from the bone Minima/Maxima + the FMRS sliders."""
    if bone_regions is None:
        return {}
    regions = _npc_regions(npc)
    if not regions:
        return {}
    from .facebones import bone_delta_matrix
    deltas: dict = {}
    for fmri, fmrs in regions:
        for bone, mn, mx in bone_regions.bones_for_fmri(race_edid, sex, fmri):
            deltas[bone] = bone_delta_matrix(mn, mx, fmrs).tolist()
    return deltas


def build_facegen_for_patch(patch, plugin_set, data_dir,
                            fallback_dir: Optional[str] = None,
                            output_dir: Optional[str] = None,
                            limit: Optional[int] = None,
                            output_size: Optional[int] = None,
                            only_npc: Optional[set] = None,
                            make_png: bool = False,
                            bake_aux: bool = False,
                            bake_nif: bool = True,
                            exclude_hdpt_types: tuple = (),
                            extractor=None,
                            templates=None,
                            pools=None,
                            races_by_edid=None,
                            resolver=None,
                            base_heads=None,
                            race_morphs=None,
                            bone_regions=None,
                            workers: Optional[int] = None,
                            throttle: bool = False,
                            progress=None,
                            cancel_event=None) -> dict:
    """Bake the FaceCustomization texture set + facegeom nif for every NPC
    override in `patch`.

    `exclude_hdpt_types` drops head parts of those PNAM type codes from the
    assembled nif (e.g. {7} Meatcaps for the preview — the bloody neck cap the
    full bake keeps for decapitation).

    `output_dir` defaults to `data_dir`. `only_npc` restricts to a set of
    EditorIDs. `make_png` also writes a PNG beside each DDS for eyeballing.
    `bake_aux` writes a per-NPC copy of the base head normal (_msn) and
    specular (_s) and points the baked head at them. Default off: the head
    points at the shared race base-head maps instead (see module docstring) —
    these maps are race-constant, so per-NPC copies just waste VRAM.
    `bake_nif` assembles the facegeom nif (else texture-only).

    The plugin-set-scoped indexes (`extractor`, `templates`, `pools`,
    `races_by_edid`) and the `resolver` / `base_heads` may be supplied
    pre-built — the preview session builds them once and reuses them across
    bakes (the AssetResolver's BA2 scan and the tint/head indexes are the
    expensive part). Any left as None are built here; a resolver built here is
    closed on return, an injected one is left open for its owner to manage.

    `workers` sets the bake parallelism (default: auto — min(16, cpu-1), or 1
    when `throttle`). The parent resolves each NPC's job serially (cheap plugin-
    set lookups) and a `ProcessPoolExecutor` bakes them; `throttle` runs a single
    BELOW_NORMAL worker so the box stays usable. Returns a stats dict.
    """
    from .headparts_resolve import resolve_headparts, HDPT_FACE

    out_root = Path(output_dir) if output_dir else Path(data_dir)
    facecust_root = out_root.joinpath(*_FACECUST_DIR.split("/"))
    facegeom_root = out_root.joinpath(*_FACEGEOM_DIR.split("/"))
    # Output dirs are per-base-plugin (the engine looks under the defining
    # plugin), cached so we don't rebuild the Path per NPC.
    _tex_dirs: dict[str, Path] = {}
    _nif_dirs: dict[str, Path] = {}

    def out_dir_for(plugin: str) -> Path:
        d = _tex_dirs.get(plugin)
        if d is None:
            d = facecust_root / plugin
            _tex_dirs[plugin] = d
        return d

    def nif_dir_for(plugin: str) -> Path:
        d = _nif_dirs.get(plugin)
        if d is None:
            d = facegeom_root / plugin
            _nif_dirs[plugin] = d
        return d

    if extractor is None:
        extractor = FactExtractor(plugin_set)
    if templates is None:
        templates = RaceTintTemplates(plugin_set)
    if pools is None:
        pools = HeadpartPools(plugin_set)
    if races_by_edid is None:
        races_by_edid = {}
        for pl in plugin_set:
            for r in pl.get_records_by_signature("RACE"):
                if r.editor_id:
                    races_by_edid[r.editor_id] = r

    # CLFM FormID -> hair-color palette scale (CNAM is a float 0..1, the
    # greyscale-to-palette lookup position). An NPC's HCLF points at one of
    # these; the assembled hair shape uses it to colour the grayscale hair.
    clfm_scale: dict = {}
    for pl in plugin_set:
        for c in pl.get_records_by_signature("CLFM"):
            cnam = c.get_subrecord("CNAM")
            if cnam is not None and cnam.size >= 4:
                clfm_scale[c.normalize_form_id(c.form_id).value] = \
                    struct.unpack("<f", cnam.data[:4])[0]

    def hair_scale_for(npc):
        hclf = npc.get_subrecord("HCLF")
        if hclf is None or hclf.size < 4:
            return None
        fid = npc.normalize_form_id(hclf.get_form_id()).value
        return clfm_scale.get(fid)

    stats = {"baked": 0, "aux": 0, "nif": 0, "nif_failed": 0,
             "no_base": 0, "no_layers": 0, "skipped": 0}

    own_resolver = resolver is None
    if own_resolver:
        resolver = AssetResolver.for_data_dir(
            Path(data_dir), Path(fallback_dir) if fallback_dir else None)
    if base_heads is None:
        base_heads = BaseHeadTextures(pools, resolver,
                                      races_by_edid=races_by_edid)

    def _resolve_info(npc):
        """Parent-side resolution: turn one patch NPC into a fully picklable
        bake job (resolved base textures, tint layers, head-part entries, output
        paths, flags), or a skip-reason key. All plugin-set-dependent work
        happens here so the bake workers never touch the plugin set."""
        race_edid = extractor.race_of(npc)
        if not race_edid:
            return None, "skipped"
        sex = Sex.FEMALE if extractor.is_female(npc) else Sex.MALE
        base = base_heads.get(race_edid, sex)
        if base is None:
            return None, "no_base"
        layers = npc_tint_layers(npc, race_edid, sex, templates)
        if not layers:
            # No tint layers (e.g. minimal children) is NOT a skip: the furry
            # head SHAPE differs from vanilla, so the nif must still bake, and a
            # base-only diffuse (composite of base + no layers = base) keeps the
            # baked face shape's referenced texture present. Counted for info.
            stats["no_layers"] += 1
        form_id = f"{npc.form_id.value & 0xFFFFFF:08X}"
        plugin = base_plugin_for(npc, patch)
        info = {
            "edid": npc.editor_id or form_id, "form_id": form_id,
            "plugin": plugin, "base": base, "layers": layers,
            "out_dir": str(out_dir_for(plugin)), "output_size": output_size,
            "make_png": make_png, "bake_aux": bake_aux, "bake_nif": bake_nif,
            "headparts": None, "nif_path": None, "hair_palette_scale": None,
            # Race per-sex height: the CK scales the baked facegen skeleton by it.
            # Needed so nif-local cloth-hair bones land in the actor frame (see
            # assemble.build_facegen_nif). 1.0 when the race omits a height.
            "bone_scale": race_height(races_by_edid.get(race_edid),
                                      sex == Sex.FEMALE),
            # NPC skin-tone RGBA (0-1) from QNAM — CK bakes it into a Skin-Tint
            # shape's skinTintColor (the furry horn base). None if no QNAM.
            "skin_tone": _npc_skin_tone(npc),
        }
        if bake_nif:
            headparts = resolve_headparts(
                npc, races_by_edid.get(race_edid), plugin_set,
                sex == Sex.FEMALE)
            if exclude_hdpt_types:
                headparts = [h for h in headparts
                             if h.get("hdpt_type") not in exclude_hdpt_types]
            # Chargen face morphs: read the NPC's MSDK/MSDV back out, resolve
            # each preset key (MPPI) to its chargen .tri shape-key name (MPPM),
            # and hang the (name, weight) list on the Face head part so the bake
            # can deform its verts. Region (FMRI/FMRS) baking is a later phase.
            morphs = _npc_morphs(npc, race_edid, sex, race_morphs)
            fb_deltas = _facebone_deltas(npc, race_edid, sex, bone_regions)
            if morphs or fb_deltas:
                # Attach the NPC's morphs to EVERY head part, not just the Face:
                # a separate part (e.g. the deer mouth) must deform with the head
                # or it detaches from the morphed snout/jaw. Each part applies
                # them through its OWN chargen tri + facebones nif; parts lacking
                # those (or with a vert-count mismatch) are skipped by the guards
                # in `assemble`, so this is safe across all parts.
                for h in headparts:
                    if morphs:
                        h["morphs"] = morphs
                    if fb_deltas:
                        h["facebone_deltas"] = fb_deltas
            info["headparts"] = headparts
            info["nif_path"] = str(nif_dir_for(plugin) / f"{form_id}.nif")
            info["hair_palette_scale"] = hair_scale_for(npc)
        return info, None

    from ._worker import (_WorkItem, bake_from_info, pick_worker_count)
    from ..session import _check_cancel

    try:
        # Resolve phase (serial, parent): cheap record/path lookups.
        work = []
        for npc in patch.get_records_by_signature("NPC_"):
            if only_npc is not None and (npc.editor_id or "") not in only_npc:
                continue
            info, skip = _resolve_info(npc)
            if skip is not None:
                # Name the NPC + reason so a skipped bake (notably "no_base":
                # the race resolved no head) is a real per-NPC report, not just
                # an aggregate count — the previewer points the user here.
                if skip in ("no_base", "skipped"):
                    log.warning("facegen: no nif for %s (%08X) - %s "
                                "(race %s)", npc.editor_id or "?",
                                npc.form_id.value & 0xFFFFFF, skip,
                                extractor.race_of(npc) or "?")
                stats[skip] += 1
                continue
            work.append(info)
            if limit is not None and len(work) >= limit:
                break

        # Ensure every per-plugin output dir exists before any worker writes.
        for d in set(_tex_dirs.values()) | set(_nif_dirs.values()):
            d.mkdir(parents=True, exist_ok=True)

        total = len(work)
        if progress is not None:
            progress("Baking FaceGen", 0, total)
        # Bake phase: serial when a single worker (or trivial work), else pooled.
        n_workers = pick_worker_count(throttle) if workers is None \
            else max(1, workers)
        if n_workers <= 1 or len(work) <= 1:
            for i, info in enumerate(work):
                _check_cancel(cancel_event)
                _accumulate(stats, bake_from_info(info, resolver))
                if progress is not None:
                    progress("Baking FaceGen", i + 1, total)
        else:
            log.info("facegen: baking %d NPCs across %d workers%s",
                     len(work), n_workers, " (throttled)" if throttle else "")
            _bake_pooled([_WorkItem(i["edid"], i) for i in work],
                         n_workers, throttle, str(data_dir), stats,
                         fallback_dir=fallback_dir,
                         progress=progress, cancel_event=cancel_event)
    finally:
        if own_resolver:
            resolver.close()
    log.info("facegen: %d FaceCustomization diffuse (+%d aux), %d nifs "
             "(%d failed); %d no-base, %d base-only (no tints), %d skipped",
             stats["baked"], stats["aux"], stats["nif"], stats["nif_failed"],
             stats["no_base"], stats["no_layers"], stats["skipped"])
    return stats


def _accumulate(stats: dict, r) -> None:
    """Fold one `_Result` into the running stats dict."""
    stats["baked"] += r.baked
    stats["aux"] += r.aux
    stats["nif"] += r.nif
    stats["nif_failed"] += r.nif_failed
    stats["skipped"] += r.skipped


def _bake_pooled(items, n_workers: int, throttle: bool, data_dir: str,
                 stats: dict, fallback_dir: Optional[str] = None,
                 progress=None, cancel_event=None) -> None:
    """Bake `items` across `n_workers` spawned processes, forwarding worker logs
    to the parent's handlers (file / stream / GUI pane) via a QueueListener so
    progress is visible everywhere the serial path's logs were.

    On cancel, the executor is shut down with `cancel_futures=True` so queued
    (not-yet-started) bakes are dropped rather than drained — only the handful
    already in flight finish."""
    import logging.handlers
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    from ._worker import _worker_init, _bake_one
    from ..session import _check_cancel, CancelledError

    ctx = mp.get_context("spawn")
    log_queue = ctx.Queue()
    level = logging.getLogger().getEffectiveLevel()
    listener = logging.handlers.QueueListener(
        log_queue, *logging.getLogger().handlers, respect_handler_level=True)
    listener.start()
    total = len(items)
    try:
        with ProcessPoolExecutor(
                max_workers=n_workers, mp_context=ctx,
                initializer=_worker_init,
                initargs=(data_dir, throttle, log_queue, level,
                          fallback_dir)) as ex:
            try:
                for i, r in enumerate(ex.map(_bake_one, items, chunksize=1)):
                    _accumulate(stats, r)
                    if progress is not None:
                        progress("Baking FaceGen", i + 1, total)
                    _check_cancel(cancel_event)
            except CancelledError:
                ex.shutdown(wait=True, cancel_futures=True)
                raise
    finally:
        listener.stop()
