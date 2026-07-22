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
from ..templates import (
    is_templated_leaf, resolve_trait_owners, traits_injection_node,
)
from ..variants import plan_injections
from ..tints import RaceTints
from ..facegen import base_plugin_for, build_facegen_for_patch
from ..facegen.assets import AssetResolver
from ..facegen.basehead import BaseHeadTextures
from ..facegen.extract import RaceTintTemplates

log = logging.getLogger(__name__)


def _skin_tone_hex(record) -> Optional[str]:
    """`#RRGGBB` of a furrified NPC override's QNAM (skin tone), or None.
    QNAM is 4 floats (r,g,b,alpha) in 0-1."""
    import struct
    q = record.get_subrecord("QNAM")
    if q is None or len(q.data) < 12:
        return None
    r, g, b = struct.unpack_from("<fff", bytes(q.data), 0)
    c = lambda v: max(0, min(255, round(v * 255)))
    return f"#{c(r):02x}{c(g):02x}{c(b):02x}"


class PreviewResult:
    __slots__ = ("nif_path", "facecust_dir", "race_name", "editor_id",
                 "parent_race", "breed", "template_owner", "template_count",
                 "template_index", "skin_tone", "owner_formid", "variant_edid")

    def __init__(self, nif_path, facecust_dir, race_name, editor_id,
                 parent_race=None, breed=None,
                 template_owner=None, template_count=0, template_index=0,
                 skin_tone=None, owner_formid=None, variant_edid=None):
        self.nif_path = nif_path
        self.facecust_dir = facecust_dir
        self.race_name = race_name
        self.editor_id = editor_id
        # The furry ENGINE race the NPC was assigned (e.g. FFODeerRace) and, if
        # a breed (a visual flavor of that race) was rolled, its name — so the
        # preview can report "Breed: X" or fall back to "Race: X".
        self.parent_race = parent_race
        self.breed = breed
        # "#RRGGBB" of the furrified NPC's skin tone (QNAM), for tinting
        # FaceGen RGB-tint shapes (horn bases etc.) in the preview. None if
        # the record carries no QNAM.
        self.skin_tone = skin_tone
        # When the previewed NPC inherits its look from a template, the trait-
        # owner actually being shown, how many distinct furrifiable owners it
        # could resolve to (the in-game variety), and the 0-based index of the
        # one shown (for the "face X of N" walk-through). 0/None for a normal NPC.
        self.template_owner = template_owner
        self.template_count = template_count
        self.template_index = template_index
        # Identity of the exact face shown: the trait-owner (base record) FormID,
        # and — when a variant is shown — the EDID that variant is minted with at
        # run time (e.g. "CompanionDeacon_F02"). Both None for a normal NPC.
        self.owner_formid = owner_formid
        self.variant_edid = variant_edid


class PreviewSession:
    """Load the plugin set + scheme once; bake individual NPCs on demand."""

    def __init__(self, scheme_name: str, data_dir: Optional[str] = None,
                 races_dir: Optional[str] = None,
                 plugins: Optional[list] = None,
                 world: Optional["FurryWorld"] = None):
        # Compose a shared FurryWorld (the GUI passes the same instance the Run
        # uses, so plugins load once). Build our own only when none is given
        # (standalone preview / tests); then we own it and close it.
        self._owns_world = world is None
        if world is None:
            from ..world import FurryWorld
            world = FurryWorld(scheme_name, data_dir=data_dir,
                               races_dir=races_dir, plugins=plugins)
        self.world = world

        # Alias the immutable loaded state so the bake methods below read it
        # directly. (`scheme`/`_facts_for`/`resolved_race` come from the world
        # too, so a single source of truth.)
        w = world
        self.data = w.data
        self.scheme = w.scheme
        self.cust = w.cust
        self.ps = w.ps
        self.extractor = w.extractor
        self.races = w.races
        self.headpart_pools = w.headpart_pools
        self.race_tints = w.race_tints
        self.race_morphs = w.race_morphs
        self.bone_regions = w.bone_regions
        self.tint_templates = w.tint_templates
        self.resolver = w.resolver
        self.base_heads = w.base_heads
        self.races_by_edid = w.races_by_edid
        self.winning = w.winning
        self.base_winning = w.base_winning
        self.winning_lvln = w.winning_lvln
        self.furrified = w.furrified
        self._npc_by_edid = w._npc_by_edid
        self._facts_cache = w._facts_cache
        self._facts_for = w._facts_for
        self.resolved_race = w.resolved_race

        # Placed-actor instance counts per trait-owner (lazy — the ACHR scan is
        # only needed once a templated NPC is previewed), and a per-leaf cache of
        # the resolved face-option list.
        self._injections: Optional[dict] = None
        self._options_cache: dict = {}

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

    # `resolved_race` and `_facts_for` are aliased to the world's in __init__.

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

    def _ensure_injections(self) -> dict:
        """The variant-injection plan the run uses (node_obj -> InjectionPlan),
        from the placed-actor scan. Computed once, lazily."""
        if self._injections is None:
            self._injections = plan_injections(
                self.ps, self.base_winning, self.winning_lvln)
        return self._injections

    def _face_options(self, objid: int, npc) -> list:
        """The true set of faces a templated `npc` can spawn with, matching what
        the run produces. If its chain is diversified by an injection (its
        injection node is planned), the faces are the K variants minted at that
        node — each copied from the same owner base, edid `<node>_F##`. Otherwise
        each furrifiable trait-owner contributes its one canonical face. Each
        option is (copy_from_objid, label_edid, signature, variant_index|None).
        Empty for a non-templated NPC. Cached per leaf."""
        cached = self._options_cache.get(objid)
        if cached is not None:
            return cached
        opts = []
        if is_templated_leaf(npc):
            injections = self._ensure_injections()
            node = traits_injection_node(
                npc, objid, self.base_winning, self.winning_lvln)
            plan = injections.get(node)
            if plan is not None:
                node_rec = (self.base_winning.get(node)
                            or self.winning.get(node))
                node_edid = ((node_rec.editor_id if node_rec else None)
                             or f"{node:08X}")
                for i in range(plan.k):
                    opts.append((plan.variant_base, node_edid,
                                 f"{node_edid}_F{i:02d}", i))
            else:
                for owner_obj, owner_edid in self.furrifiable_owners(npc):
                    opts.append((owner_obj, owner_edid,
                                 self.scheme.signature_for(owner_edid), None))
        self._options_cache[objid] = opts
        return opts

    def bake(self, objid: int, temp_root: Path,
             facegen_size: int = 512,
             refurrify: bool = True,
             variant: int = 0) -> Optional[PreviewResult]:
        """Furrify NPC `objid` into a throwaway patch and bake its facegen into
        `temp_root`. Returns a PreviewResult, or None if the NPC isn't
        furrifiable (gated, left human, or no child race).

        If the NPC was already furrified by an earlier run and `refurrify` is
        False, the existing furry override is baked verbatim ("show what was
        done") instead of re-rolling from the vanilla base.

        **Templated (Use-Traits) NPCs** take their look from a trait-owner via
        the template chain, so we bake the OWNER (what the engine actually
        shows), not the leaf. Such an NPC has N possible faces (`_face_options`);
        `variant` selects which one (0 = the first, wrapped into range), so the
        pane can step ◀ ▶ through all of them. The result carries
        `template_owner`, `template_count` (= N) and `template_index`.
        """
        temp_root = Path(temp_root)
        patch = Plugin.new_plugin(str(temp_root / "FO4FurryPreview.esp"),
                                  masters=[], game="fo4")
        patch.plugin_set = self.ps

        template_owner = None
        template_count = 0
        template_index = 0
        owner_formid = None
        variant_edid = None
        parent_race = None
        breed_name = None
        breed_signature = None      # family-shared breed key (non-templated path)

        if objid in self.furrified and not refurrify:
            # Show the existing result: copy the already-furry winning record
            # into the throwaway patch and bake straight from it.
            existing = self.winning.get(objid)
            if existing is None:
                raise KeyError(f"NPC {objid:08X} not in load order")
            override = patch.copy_record(existing, existing.plugin)
            race_name = self.extractor.race_of(override) or ""
            # Reading back an already-furry NPC: we know its engine race but not
            # which breed produced it, so report the race.
            parent_race = race_name
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
            # facegen path both match. `variant` selects which face (◀ ▶ steps).
            minted = False
            opts = self._face_options(objid, npc)
            if opts:
                template_count = len(opts)
                template_index = variant % len(opts)
                owner_obj, owner_edid, signature, vidx = opts[template_index]
                owner_base = (self.base_winning.get(owner_obj)
                              or self.winning.get(owner_obj))
                owner_formid = owner_base.normalize_form_id(
                    owner_base.form_id).value
                if vidx is None:
                    target = owner_base
                    template_owner = owner_edid
                else:
                    target = patch.copy_record(owner_base, owner_base.plugin,
                                               new_form_id=True)
                    target.editor_id = signature
                    minted = True
                    template_owner = owner_edid
                    variant_edid = signature   # the run-time variant EDID, e.g. FooTemplate_F02
            elif is_templated_leaf(npc):
                # Templated, but no owner resolves furry — nothing to show.
                return None
            else:
                target = npc
                signature = self.scheme.signature_for(npc.editor_id or "")
                # Family members share a breed (rolled on the leader's key) while
                # varying headparts/tints via their own signature — matches the
                # run (session.do_furrify passes breed_signature_for).
                breed_signature = self.scheme.breed_signature_for(
                    npc.editor_id or "")

            race_name = self.resolved_race(target)
            if race_name is None:
                return None
            # Resolve + roll the breed (a visual flavor of the engine race) so
            # the preview shows the actual breed an NPC would spawn with; the
            # displayed race name becomes the breed when one applies.
            parent_race, breed = self.cust.resolve_race_or_breed(race_name)
            if breed is None:
                breed = self.cust.roll_breed(breed_signature or signature,
                                             parent_race)
            breed_name = breed.name if breed else None
            race_name = breed_name or parent_race
            is_child = is_child_npc(self.extractor, target)
            furry_race = self.races.resolve(parent_race, is_child)
            if furry_race is None:
                return None
            sex = Sex.FEMALE if self.extractor.is_female(target) else Sex.MALE
            if minted:
                apply_furry(patch, target, furry_race, race_edid=parent_race,
                            sex=sex, signature=signature,
                            headpart_pools=self.headpart_pools,
                            race_tints=self.race_tints, customization=self.cust,
                            breed_name=breed_name,
                            race_morphs=self.race_morphs,
                            minimal=is_child)
                override = target
            else:
                override = furrify_npc(patch, target, furry_race,
                                       race_edid=parent_race, sex=sex,
                                       signature=signature,
                                       headpart_pools=self.headpart_pools,
                                       race_tints=self.race_tints,
                                       customization=self.cust,
                                       breed_name=breed_name,
                                       race_morphs=self.race_morphs,
                                       minimal=is_child)
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
                                base_heads=self.base_heads,
                                race_morphs=self.race_morphs,
                                bone_regions=self.bone_regions)

        # The baked files are keyed on the record we actually furrified (the
        # owner for a templated leaf), not the picked leaf.
        plugin = base_plugin_for(override, patch)
        fid = f"{override.form_id.value & 0xFFFFFF:08X}"
        nif_path = (temp_root / "meshes" / "Actors" / "Character" /
                    "FaceGenData" / "FaceGeom" / plugin / f"{fid}.nif")
        facecust_dir = (temp_root / "textures" / "Actors" / "Character" /
                        "FaceCustomization" / plugin)
        return PreviewResult(nif_path, facecust_dir, race_name, display_edid,
                             parent_race=parent_race, breed=breed_name,
                             template_owner=template_owner,
                             template_count=template_count,
                             template_index=template_index,
                             owner_formid=owner_formid,
                             variant_edid=variant_edid,
                             skin_tone=_skin_tone_hex(override))


    def close(self) -> None:
        """Release the loaded world (BA2 handles + extraction temp dir) — but
        ONLY if we built it ourselves. When the GUI passed in a shared world it
        owns its lifetime, so we leave it alone. Safe to call more than once."""
        if self._owns_world:
            world = getattr(self, "world", None)
            if world is not None:
                world.close()
        self.resolver = None
        self.world = None
