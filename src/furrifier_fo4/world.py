"""The shared loaded world: plugin set + every index, built once.

Both the live preview and the full Run need the same expensive load — the
plugin set (`load_all`, ~20-30s), the per-record indexes (extractor, race
library, headpart/tint/morph pools), the FacialBoneRegions, and the facegen
indexes (tint templates, AssetResolver's BA2 scan, base-head textures). A
`FurryWorld` does that once; the GUI owns one and hands the SAME instance to the
preview worker and the Run worker, so a preview followed by a Run pays the load
cost a single time.

The Run does NOT mutate the plugin set — it reads `ps` and writes into a
separate patch object (`patch.plugin_set = ps` is only a back-reference) — so
the world is safe to reuse across previews and repeated Runs without
invalidation. The Run furrifies from `base_winning` (the winner among
non-furrifier plugins), so it never needs the output patch stripped from the
load order.

`scheme` is part of the world (resolution needs it). A different scheme means a
different world; the load itself is scheme-independent, but to keep this simple
the GUI keys its cache on (data, plugins, scheme) and rebuilds on a scheme
change — the same cost the preview already paid before this existed.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from esplib import LoadOrder, PluginSet, find_game_data, find_strings_dir

from .customization import load_customization
from .extract import FactExtractor
from .furrify import RaceLibrary
from .headparts import HeadpartPools
from .loader import load_scheme
from .models import NON_FURRY_TARGETS, is_furrifier_plugin
from .tints import RaceTints
from .facemorphs import RaceMorphs, FacialBoneRegions
from .facegen.assets import AssetResolver
from .facegen.basehead import BaseHeadTextures
from .facegen.extract import RaceTintTemplates

log = logging.getLogger(__name__)


def default_races_dir() -> Path:
    """The bundled race catalog dir. Frozen (PyInstaller): the `races/` folder
    copied loose next to the exe; dev: the package's sibling `races/` folder.

    Routes through loader._find_resource_dir so it agrees with how schemes/ and
    builtin.toml are located — otherwise a frozen kit looks for races/ inside the
    bundle (via __file__) and never finds the loose, user-editable copy.
    """
    from .loader import _find_resource_dir
    found = _find_resource_dir("races")
    if found is not None:
        return found
    return Path(__file__).resolve().parent.parent.parent / "races"


class FurryWorld:
    """Plugin set + every index a preview or Run needs, loaded once."""

    def __init__(self, scheme_name: str, data_dir: Optional[str] = None,
                 races_dir: Optional[str] = None,
                 plugins: Optional[list] = None,
                 progress=None):
        def emit(msg):
            if progress is not None:
                progress(msg)

        # `data_dir` (the --resources override) is searched FIRST for plugins
        # and assets; the real game Data is the fallback for anything not there.
        # When no override is given, the game Data is the sole root (no fallback).
        game = Path(find_game_data("fo4"))
        override = Path(data_dir) if data_dir else None
        self.data = override or game
        self.fallback = game if override is not None else None
        self.scheme = load_scheme(scheme_name)
        self.scheme.build_indexes()
        self.cust = load_customization(Path(races_dir) if races_dir
                                       else default_races_dir())

        if plugins is None:
            plugins = list(LoadOrder.from_game("fo4", active_only=True))
        lo = LoadOrder.from_list(
            plugins, data_dir=str(self.data),
            fallback_dir=str(self.fallback) if self.fallback else None)
        self.ps = PluginSet(lo)
        strings = find_strings_dir("fo4")
        for p in self.ps:
            p.string_search_dirs = [str(strings)] if strings else []
        emit("Loading plugins…")
        self.ps.load_all()
        log.info("loaded %d plugins", len(list(self.ps)))

        # Per-record indexes (scheme-independent).
        self.extractor = FactExtractor(self.ps)
        self.races = RaceLibrary(self.ps, child_races=self.cust.child_races)
        # Pools are built scheme-independently (they describe every race
        # honestly); the scheme's exclude list only filters at pick time.
        self.headpart_pools = HeadpartPools(
            self.ps, exclude=self.scheme.exclude_headparts)
        self.race_tints = RaceTints(self.ps)
        self.race_morphs = RaceMorphs(self.ps)
        self.bone_regions = FacialBoneRegions(self.data, self.fallback)

        # Validate the catalog against the real race data now that both are
        # loaded — warns (with name suggestions) on facemorph presets/regions
        # and tint colors a race doesn't actually offer, instead of leaving them
        # to silently drop at bake time. See validate.py.
        from .validate import validate_customization
        validate_customization(self.cust, self.race_morphs, self.race_tints,
                               self.bone_regions)

        self.races_by_edid: dict = {}
        for plugin in self.ps:
            for r in plugin.get_records_by_signature("RACE"):
                if r.editor_id:
                    self.races_by_edid[r.editor_id] = r

        # Facegen indexes (the AssetResolver BA2 scan is the expensive one).
        self.tint_templates = RaceTintTemplates(self.ps)
        self.resolver = AssetResolver.for_data_dir(self.data, self.fallback)
        self.base_heads = BaseHeadTextures(self.headpart_pools, self.resolver,
                                           races_by_edid=self.races_by_edid)

        # `winning` = absolute winner per objid (incl. furrifier output).
        # `base_winning` = winner among NON-furrifier plugins (the vanilla/mod
        # record to furrify). `furrified` = objids whose absolute winner is
        # itself furrifier output (already done by an earlier run). The Run
        # furrifies base_winning; in preserve mode it skips `furrified`.
        self.winning: dict = {}
        self.base_winning: dict = {}
        self.winning_lvln: dict = {}
        self.furrified: set = set()
        for plugin in self.ps:
            furrifier = is_furrifier_plugin(plugin)
            for npc in plugin.get_records_by_signature("NPC_"):
                objid = npc.form_id.value & 0xFFFFFF
                self.winning[objid] = npc
                if furrifier:
                    self.furrified.add(objid)
                else:
                    self.base_winning[objid] = npc
                    self.furrified.discard(objid)
            for lvln in plugin.get_records_by_signature("LVLN"):
                self.winning_lvln[lvln.form_id.value & 0xFFFFFF] = lvln

        self._npc_by_edid: dict = {}
        for npc in self.base_winning.values():
            if npc.editor_id:
                self._npc_by_edid[npc.editor_id] = npc
        self._facts_cache: dict = {}

    def _facts_for(self, edid):
        if edid not in self._facts_cache:
            n = self._npc_by_edid.get(edid)
            self._facts_cache[edid] = (
                self.extractor.facts_for(
                    n, signature=self.scheme.signature_for(edid))
                if n is not None else None)
        return self._facts_cache[edid]

    def resolved_race(self, npc) -> Optional[str]:
        """The scheme's furry race for a BASE `npc`, or None if gated/human."""
        facts = (self._facts_for(npc.editor_id or "")
                 or self.extractor.facts_for(npc))
        race_name = self.scheme.resolve_race(facts, self._facts_for)
        if race_name is None or race_name in NON_FURRY_TARGETS:
            return None
        return race_name

    def close(self) -> None:
        """Release the AssetResolver (BA2 handles + temp dir). Idempotent."""
        resolver = getattr(self, "resolver", None)
        if resolver is not None:
            resolver.close()
            self.resolver = None


def _world_key(scheme: str, data_dir: Optional[str], plugins: Optional[list]):
    return (scheme, data_dir or "",
            tuple(p.lower() for p in plugins) if plugins else None)


class WorldCache:
    """Holds one FurryWorld, keyed by (scheme, data dir, plugin list), shared by
    the GUI's preview worker and Run worker (different threads) so plugins load
    once. The Run doesn't mutate the world (it reads the plugin set, writes a
    separate patch), so no post-Run invalidation is needed; a config change just
    rebuilds on the next request. Lock-guarded for the cross-thread get/build."""

    def __init__(self):
        self._world: Optional[FurryWorld] = None
        self._key = None
        self._lock = threading.Lock()

    def get_or_build(self, scheme: str, data_dir: Optional[str],
                     plugins: Optional[list], progress=None) -> FurryWorld:
        key = _world_key(scheme, data_dir, plugins)
        with self._lock:
            if self._world is not None and self._key == key:
                return self._world
            if self._world is not None:
                self._world.close()
                self._world = None
            self._world = FurryWorld(scheme, data_dir=data_dir, plugins=plugins,
                                     progress=progress)
            self._key = key
            return self._world

    def close(self) -> None:
        with self._lock:
            if self._world is not None:
                self._world.close()
                self._world = None
                self._key = None
