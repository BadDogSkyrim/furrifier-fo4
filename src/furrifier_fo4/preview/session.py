"""Reusable preview session: load once, bake one NPC on demand.

Mirrors the per-NPC resolution in `session.run` but for a single NPC, so the
GUI can preview a furrified head without a full run. Plugin loading + index
building happen once in __init__ (~15s); each `bake()` furrifies one NPC into
a throwaway patch and assembles its facegeom + texture into a temp dir.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from esplib import LoadOrder, PluginSet, Plugin, find_game_data, find_strings_dir

from ..customization import load_customization
from ..extract import FactExtractor
from ..furrify import RaceLibrary, furrify_npc, apply_furry, is_child_npc
from ..headparts import HeadpartPools
from ..loader import load_scheme
from ..models import (
    FURRIFIABLE_RACES, NON_FURRY_TARGETS, Sex, is_furrifier_plugin,
)
from ..templates import is_templated_leaf, resolve_trait_owners
from ..variants import count_instances, variant_count, EXPAND_THRESHOLD
from ..tints import RaceTints
from ..facegen import base_plugin_for, build_facegen_for_patch
from ..facegen.assets import AssetResolver
from ..facegen.basehead import BaseHeadTextures
from ..facegen.extract import RaceTintTemplates

log = logging.getLogger(__name__)


class PreviewResult:
    __slots__ = ("nif_path", "facecust_dir", "race_name", "editor_id",
                 "template_owner", "template_count", "template_index")

    def __init__(self, nif_path, facecust_dir, race_name, editor_id,
                 template_owner=None, template_count=0, template_index=0):
        self.nif_path = nif_path
        self.facecust_dir = facecust_dir
        self.race_name = race_name
        self.editor_id = editor_id
        # When the previewed NPC inherits its look from a template, the trait-
        # owner actually being shown, how many distinct furrifiable owners it
        # could resolve to (the in-game variety), and the 0-based index of the
        # one shown (for the "face X of N" walk-through). 0/None for a normal NPC.
        self.template_owner = template_owner
        self.template_count = template_count
        self.template_index = template_index


class PreviewSession:
    """Load the plugin set + scheme once; bake individual NPCs on demand."""

    def __init__(self, scheme_name: str, data_dir: Optional[str] = None,
                 races_dir: Optional[str] = None,
                 plugins: Optional[list] = None):
        self.data = Path(data_dir or find_game_data("fo4"))
        self.scheme = load_scheme(scheme_name)
        self.scheme.build_indexes()
        if races_dir is None:
            races_dir = Path(__file__).resolve().parents[2] / "races"
        self.cust = load_customization(Path(races_dir))

        if plugins is None:
            plugins = list(LoadOrder.from_game("fo4", active_only=True))
        lo = LoadOrder.from_list(plugins, data_dir=str(self.data))
        self.ps = PluginSet(lo)
        strings = find_strings_dir("fo4")
        for p in self.ps:
            p.string_search_dirs = [str(strings)] if strings else []
        self.ps.load_all()

        self.extractor = FactExtractor(self.ps)
        self.races = RaceLibrary(self.ps, child_races=self.cust.child_races)
        self.headpart_pools = HeadpartPools(self.ps)
        self.race_tints = RaceTints(self.ps)

        # Facegen-bake indexes, built once and reused across bakes — the
        # AssetResolver's BA2 scan and the tint-template index are the
        # expensive part of a bake (the reason a cold bake was ~7.7s).
        self.tint_templates = RaceTintTemplates(self.ps)
        self.resolver = AssetResolver.for_data_dir(self.data)
        self.base_heads = BaseHeadTextures(self.headpart_pools, self.resolver)
        self.races_by_edid: dict = {}
        for plugin in self.ps:
            for r in plugin.get_records_by_signature("RACE"):
                if r.editor_id:
                    self.races_by_edid[r.editor_id] = r

        # Winning NPC per object id. `winning` is the absolute winner (incl.
        # furrifier output); `base_winning` is the winner among non-furrifier
        # plugins — the vanilla/mod record to furrify. `furrified` is the set
        # of objids whose absolute winner is itself furrifier output (already
        # done by an earlier run). Resolution + facts always use base records;
        # the existing furry override is consulted only to "show what was done".
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
        # Per-leaf walk-through cursor: Roll steps to the next face in order
        # (wrapping), so you can see each possible face one at a time.
        self._roll_index: dict = {}
        # Placed-actor instance counts per trait-owner (lazy — the ACHR scan is
        # only needed once a templated NPC is previewed), and a per-leaf cache of
        # the resolved face-option list.
        self._instances: Optional[dict] = None
        self._options_cache: dict = {}

    def _facts_for(self, edid):
        if edid not in self._facts_cache:
            n = self._npc_by_edid.get(edid)
            self._facts_cache[edid] = (
                self.extractor.facts_for(n, signature=self.scheme.signature_for(edid))
                if n is not None else None)
        return self._facts_cache[edid]

    def list_npcs(self) -> list:
        """(objid, editor_id) for every furry-relevant NPC, sorted by
        EditorID. Same membership as PreviewCatalog: base race in
        FURRIFIABLE_RACES (so already-furrified NPCs are included)."""
        out = []
        for objid, npc in self.base_winning.items():
            if npc.editor_id and self.extractor.race_of(npc) in FURRIFIABLE_RACES:
                out.append((objid, npc.editor_id))
        out.sort(key=lambda t: t[1].lower())
        return out

    def resolved_race(self, npc) -> Optional[str]:
        """The scheme's furry race for `npc` (a BASE record), or None if
        gated/left-human."""
        facts = self._facts_for(npc.editor_id or "") or self.extractor.facts_for(npc)
        race_name = self.scheme.resolve_race(facts, self._facts_for)
        if race_name is None or race_name in NON_FURRY_TARGETS:
            return None
        return race_name

    def furrifiable_owners(self, npc) -> list:
        """For a templated (Use-Traits) leaf, the distinct trait-OWNERS it can
        resolve to that are themselves furrifiable, as sorted (objid, edid).
        These are the faces the leaf could actually show in-game (the runtime
        leveled roll picks among them). Empty for a non-templated NPC."""
        if not is_templated_leaf(npc):
            return []
        out = []
        for o in resolve_trait_owners(npc, self.base_winning, self.winning_lvln):
            owner = self.base_winning.get(o) or self.winning.get(o)
            if owner is None:
                continue
            if self.resolved_race(owner) is not None:
                out.append((o, owner.editor_id or f"{o:08X}"))
        out.sort(key=lambda t: t[1].lower())
        return out

    def _ensure_instances(self) -> dict:
        """Placed-actor instance count per trait-owner (the same scan the run
        uses to size variant-expansion). Computed once, lazily."""
        if self._instances is None:
            owner_set: set = set()
            for npc in self.base_winning.values():
                if is_templated_leaf(npc):
                    owner_set |= resolve_trait_owners(
                        npc, self.base_winning, self.winning_lvln)
            self._instances = count_instances(
                self.ps, self.base_winning, self.winning_lvln, owner_set)
        return self._instances

    def _face_options(self, objid: int, npc) -> list:
        """The true set of faces a templated `npc` can spawn with, matching what
        the run produces: each trait-owner contributes its K variant signatures
        if it's variant-expanded (placed instances >= threshold), else its one
        canonical face. Each option is (owner_objid, owner_edid, signature,
        variant_index|None). Empty for a non-templated NPC. Cached per leaf."""
        cached = self._options_cache.get(objid)
        if cached is not None:
            return cached
        owners = self.furrifiable_owners(npc)
        opts = []
        if owners:
            instances = self._ensure_instances()
            for owner_obj, owner_edid in owners:
                n = instances.get(owner_obj, 0)
                if n >= EXPAND_THRESHOLD:
                    for i in range(variant_count(n)):
                        opts.append((owner_obj, owner_edid,
                                     f"{owner_edid}_F{i:02d}", i))
                else:
                    opts.append((owner_obj, owner_edid,
                                 self.scheme.signature_for(owner_edid), None))
        self._options_cache[objid] = opts
        return opts

    def bake(self, objid: int, temp_root: Path,
             facegen_size: int = 512,
             refurrify: bool = True,
             roll: bool = False) -> Optional[PreviewResult]:
        """Furrify NPC `objid` into a throwaway patch and bake its facegen into
        `temp_root`. Returns a PreviewResult, or None if the NPC isn't
        furrifiable (gated, left human, or no child race).

        If the NPC was already furrified by an earlier run and `refurrify` is
        False, the existing furry override is baked verbatim ("show what was
        done") instead of re-rolling from the vanilla base.

        **Templated (Use-Traits) NPCs** take their look from a trait-owner via
        the template chain, so we bake the OWNER (what the engine actually
        shows), not the leaf. `roll=False` shows the canonical owner on its own
        deterministic signature; `roll=True` picks a RANDOM owner from the
        reachable set on a RANDOM signature — a truthful sample of what could
        spawn (the result carries `template_owner` + `template_count`).
        """
        temp_root = Path(temp_root)
        patch = Plugin.new_plugin(str(temp_root / "FO4FurryPreview.esp"),
                                  masters=[], game="fo4")
        patch.plugin_set = self.ps

        template_owner = None
        template_count = 0
        template_index = 0

        if objid in self.furrified and not refurrify:
            # Show the existing result: copy the already-furry winning record
            # into the throwaway patch and bake straight from it.
            existing = self.winning.get(objid)
            if existing is None:
                raise KeyError(f"NPC {objid:08X} not in load order")
            override = patch.copy_record(existing, existing.plugin)
            race_name = self.extractor.race_of(override) or ""
            display_edid = existing.editor_id or f"{objid:08X}"
        else:
            npc = self.base_winning.get(objid) or self.winning.get(objid)
            if npc is None:
                raise KeyError(f"NPC {objid:08X} not in load order")

            # Templated leaf: step through its TRUE in-game face set, matching
            # what the run produces — each trait-owner contributes its K variant
            # faces if variant-expanded, else its single canonical face. A
            # variant is minted into the throwaway patch EXACTLY as the run does
            # (fresh record, edid = its signature), so its species and a distinct
            # facegen path both match. Roll advances one face; default = face 0.
            minted = False
            opts = self._face_options(objid, npc)
            if opts:
                template_count = len(opts)
                template_index = ((self._roll_index.get(objid, 0) + 1)
                                  % len(opts)) if roll else 0
                self._roll_index[objid] = template_index
                owner_obj, owner_edid, signature, vidx = opts[template_index]
                owner_base = (self.base_winning.get(owner_obj)
                              or self.winning.get(owner_obj))
                if vidx is None:
                    target = owner_base
                    template_owner = owner_edid
                else:
                    target = patch.copy_record(owner_base, owner_base.plugin,
                                               new_form_id=True)
                    target.editor_id = signature
                    minted = True
                    template_owner = f"{owner_edid} variant {vidx + 1}"
            elif is_templated_leaf(npc):
                # Templated, but no owner resolves furry — nothing to show.
                return None
            else:
                target = npc
                signature = self.scheme.signature_for(npc.editor_id or "")

            race_name = self.resolved_race(target)
            if race_name is None:
                return None
            is_child = is_child_npc(self.extractor, target)
            furry_race = self.races.resolve(race_name, is_child)
            if furry_race is None:
                return None
            sex = Sex.FEMALE if self.extractor.is_female(target) else Sex.MALE
            if minted:
                apply_furry(patch, target, furry_race, race_edid=race_name,
                            sex=sex, signature=signature,
                            headpart_pools=self.headpart_pools,
                            race_tints=self.race_tints, customization=self.cust)
                override = target
            else:
                override = furrify_npc(patch, target, furry_race,
                                       race_edid=race_name, sex=sex,
                                       signature=signature,
                                       headpart_pools=self.headpart_pools,
                                       race_tints=self.race_tints,
                                       customization=self.cust)
            display_edid = npc.editor_id or f"{objid:08X}"

        build_facegen_for_patch(patch, self.ps, str(self.data),
                                output_dir=str(temp_root),
                                output_size=facegen_size,
                                only_npc={override.editor_id or ""},
                                # Hide the meatcap (HDPT type 7 Meatcaps) — the
                                # bloody neck cap — in the preview only.
                                exclude_hdpt_types=(7,),
                                # Reuse the session-scoped indexes/resolver so
                                # each bake skips the BA2 scan + index rebuild.
                                extractor=self.extractor,
                                templates=self.tint_templates,
                                pools=self.headpart_pools,
                                races_by_edid=self.races_by_edid,
                                resolver=self.resolver,
                                base_heads=self.base_heads)

        # The baked files are keyed on the record we actually furrified (the
        # owner for a templated leaf), not the picked leaf.
        plugin = base_plugin_for(override, patch)
        fid = f"{override.form_id.value & 0xFFFFFF:08X}"
        nif_path = (temp_root / "meshes" / "Actors" / "Character" /
                    "FaceGenData" / "FaceGeom" / plugin / f"{fid}.nif")
        facecust_dir = (temp_root / "textures" / "Actors" / "Character" /
                        "FaceCustomization" / plugin)
        return PreviewResult(nif_path, facecust_dir, race_name, display_edid,
                             template_owner=template_owner,
                             template_count=template_count,
                             template_index=template_index)


    def close(self) -> None:
        """Release the session-scoped AssetResolver (closes BA2 handles and
        removes its extraction temp dir). Safe to call more than once."""
        resolver = getattr(self, "resolver", None)
        if resolver is not None:
            resolver.close()
            self.resolver = None
