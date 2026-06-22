"""Run a furrification pass: load → resolve race per NPC → write patch.

This is the orchestration layer. It builds the load order, resolves each NPC's
furry race via the scheme engine (G1), extracts the facts it needs (G2), and
writes furrified overrides (G3 furrify_npc) into a new patch plugin.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from esplib import LoadOrder, PluginSet, Plugin, find_game_data, find_strings_dir

from .extract import FactExtractor
from .furrify import RaceLibrary, furrify_npc, apply_furry, is_child_npc
from .loader import load_scheme
from .models import (
    FURRIFIABLE_RACES, FURRIFIER_AUTHOR, NON_FURRY_TARGETS, is_furrifier_plugin,
)
from .templates import (
    is_templated_leaf, resolve_trait_owners, traits_injection_node,
)
from .variants import plan_injections, expand_at_node

log = logging.getLogger(__name__)

# progress(phase_label, current, total) — total <= 0 means an indeterminate
# phase (no count, e.g. plugin load). The GUI maps this onto a phase label +
# progress bar; the CLI passes None (no callback).
ProgressCallback = Callable[[str, int, int], None]


class CancelledError(Exception):
    """Raised at a cooperative cancel checkpoint when the run's cancel_event is
    set. The GUI worker catches it to report a clean cancel; the CLI never sets
    the event, so it never fires there."""


def _check_cancel(event: Optional[threading.Event]) -> None:
    if event is not None and event.is_set():
        raise CancelledError()


# esplib mints new-record object IDs from 0x800 upward (get_next_form_id). A
# light plugin (ESL/ESPFE) can only address object IDs through 0xFFF, so a patch
# can hold at most 0xFFF - 0x800 + 1 = 2048 NEW records and still be flagged
# light. Overrides keep their master's FormID and don't consume an object ID.
_ESL_FIRST_OBJECT_ID = 0x800
ESL_MAX_NEW_RECORDS = 0x1000 - _ESL_FIRST_OBJECT_ID   # 2048


def esl_new_record_count(patch) -> int:
    """How many NEW records `patch` has minted (overrides excluded)."""
    return patch.header.next_object_id - _ESL_FIRST_OBJECT_ID


def apply_esl_flag(patch) -> tuple[bool, int]:
    """Flag `patch` light (ESL/ESPFE) iff its new records fit the ESL object-ID
    range. Returns (made_light, new_record_count); when the count exceeds
    ESL_MAX_NEW_RECORDS the flag is left off so the patch stays a full ESP. The
    file extension is unaffected either way — a light .esp is ESPFE."""
    count = esl_new_record_count(patch)
    fits = count <= ESL_MAX_NEW_RECORDS
    patch.header.is_esl = fits
    return fits, count


def furrifier_output_names(plugin_names, data_dir) -> set:
    """The subset of `plugin_names` that are furrifier output (TES4 author
    stamped FURRIFIER_AUTHOR), lowercased. Reads only each plugin's header
    (a partial parse — see Plugin.load), so it stays cheap."""
    lo = LoadOrder.from_list(list(plugin_names), data_dir=str(data_dir))
    found = set()
    for name in plugin_names:
        path = lo.plugin_path(name)
        if path is None:
            continue
        try:
            if is_furrifier_plugin(Plugin.load(path, only_signatures=frozenset())):
                found.add(name.lower())
        except Exception:
            continue
    return found


def run(scheme_name: str, patch_name: str = "FO4FurryPatch.esp",
        plugins: Optional[list[str]] = None,
        data_dir: Optional[str] = None,
        races_dir: Optional[str] = None,
        limit: Optional[int] = None,
        only_faction: Optional[str] = None,
        only_npcs: Optional[list] = None,
        output_dir: Optional[str] = None,
        bake_facegen: bool = True,
        facegen_size: Optional[int] = 1024,
        refurrify_existing: bool = True,
        variant_expansion: bool = True,
        emit_esl: bool = False,
        pack: bool = False,
        workers: Optional[int] = None,
        throttle: bool = False,
        world=None,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None) -> dict:
    """Furrify the load order with `scheme_name`, writing `patch_name`.

    `world` (a FurryWorld) supplies a pre-loaded plugin set + indexes so the GUI
    can share one load between the preview and the Run. When None, one is built
    internally from `scheme_name`/`data_dir`/`races_dir`/`plugins` (CLI/tests).

    `plugins` overrides the load order (else uses the game's active list).
    `races_dir` holds the race catalog (races/*.toml) with child_race,
    weight_range, headpart and color customization (defaults to the package's
    races/ dir).
    `limit` caps how many NPCs are furrified (for quick test runs).
    `only_faction` restricts furrification to members of the given faction(s)
    (EditorID string or list, e.g. ['SettlementDiamondCity',
    'SettlementGoodneighbor']) — a focused in-game sample.

    Returns a stats dict. The patch is saved into data_dir.
    """
    only_factions = None
    if only_faction is not None:
        only_factions = ({only_faction} if isinstance(only_faction, str)
                         else set(only_faction))
    only_npc_set = None
    if only_npcs is not None:
        only_npc_set = ({only_npcs} if isinstance(only_npcs, str)
                        else set(only_npcs))

    def emit(phase: str, current: int = 0, total: int = 0) -> None:
        """Report progress and sample the cancel flag. Phase boundaries and the
        per-NPC checkpoints are the only places a run can be cancelled — work
        inside a phase (e.g. plugin load) is opaque, so we sample between."""
        if progress is not None:
            progress(phase, current, total)
        _check_cancel(cancel_event)

    # Build the shared world if the caller didn't hand one in (CLI/tests). The
    # world loads the FULL active set (no patch stripping) and exposes
    # `base_winning` — the winner among NON-furrifier plugins — so resolution
    # always sees a vanilla/mod base record (a prior furry override never leaks
    # in to fail the candidate gate). The Run reads the plugin set and writes a
    # separate patch, so it never mutates the world.
    own_world = world is None
    if world is None:
        from .world import FurryWorld
        world = FurryWorld(scheme_name, data_dir=data_dir, races_dir=races_dir,
                           plugins=plugins, progress=lambda m: emit(m))

    data = world.data
    # Where to WRITE (patch + FaceGenData). Defaults to the read dir; point at
    # a mod-manager staging folder to keep the live Data tree clean.
    out = Path(output_dir) if output_dir else data
    scheme = world.scheme
    cust = world.cust
    ps = world.ps
    extractor = world.extractor
    races = world.races
    headpart_pools = world.headpart_pools
    race_tints = world.race_tints
    race_morphs = world.race_morphs
    bone_regions = world.bone_regions
    # Furrify from the base records; `furrified` = objids already furry (skipped
    # in preserve mode). `winning_lvln` drives the template walk.
    winning = world.base_winning
    winning_lvln = world.winning_lvln
    furrified = world.furrified
    facts_for = world._facts_for

    patch = Plugin.new_plugin(str(out / patch_name), masters=[], game='fo4')
    patch.header.author = FURRIFIER_AUTHOR
    # Join the patch to the plugin set so write_form_id / copy_record can
    # denormalize load-order-indexed FormIDs (e.g. a furry race at load-order
    # index 7) into the patch's own master-list space. Without this the high
    # byte leaks through and RNAM/WNAM point at a nonexistent master index.
    patch.plugin_set = ps

    stats = {'total': 0, 'gated': 0, 'furrified': 0, 'left_human': 0,
             'no_child_race': 0, 'preserved': 0, 'armas_patched': 0,
             'templated': 0, 'owner_furrified': 0, 'minimal_children': 0,
             'expanded_owners': 0, 'variants': 0, 'race_counts': {},
             'esl': False, 'new_records': 0, 'packed': []}
    # ghoul vanilla race EDID -> furry target race name, filled during the run.
    ghoul_targets: dict[str, str] = {}

    def do_furrify(npc) -> bool:
        """Run the gate + write a furrified override for one record. Classifies
        on the record's OWN signature (so a shared trait-owner template becomes
        the breed its own edid/factions imply). Returns True if furrified."""
        facts = facts_for(npc.editor_id or '') or extractor.facts_for(npc)
        race_name = scheme.resolve_race(facts, facts_for)
        if race_name is None:
            stats['gated'] += 1
            return False
        if race_name in NON_FURRY_TARGETS:
            stats['left_human'] += 1
            return False
        # A scheme may target a breed name directly (-> parent race + that
        # breed); otherwise the parent race is `race_name` and apply_furry rolls
        # a breed from its distribution.
        parent_race, breed = cust.resolve_race_or_breed(race_name)
        is_child = is_child_npc(extractor, npc)
        furry_race = races.resolve(parent_race, is_child)
        if furry_race is None:
            stats['no_child_race'] += 1
            return False
        # Track ghoul->furry mappings so the armor pass can fit ghoul gear.
        if facts.race in ('GhoulRace', 'GhoulChildRace'):
            ghoul_targets[facts.race] = parent_race
        # Headparts are picked from the ADULT race's pools (the engine race),
        # keyed on the NPC's appearance signature; sex from ACBS.
        from .models import Sex
        sex = Sex.FEMALE if extractor.is_female(npc) else Sex.MALE
        furrify_npc(patch, npc, furry_race,
                    race_edid=parent_race, sex=sex,
                    signature=scheme.signature_for(npc.editor_id or ''),
                    breed_signature=scheme.breed_signature_for(
                        npc.editor_id or ''),
                    headpart_pools=headpart_pools, race_tints=race_tints,
                    customization=cust,
                    breed_name=(breed.name if breed else None),
                    race_morphs=race_morphs, bone_regions=bone_regions,
                    minimal=is_child)
        if is_child:
            stats['minimal_children'] += 1
        stats['furrified'] += 1
        stats['race_counts'][parent_race] = \
            stats['race_counts'].get(parent_race, 0) + 1
        return True

    def furrify_variant(record, signature) -> bool:
        """Resolve a race on `signature` (the variant's own EditorID, so each
        variant rolls its own species) and apply the furry appearance to the
        already-minted variant record in place. Returns True if furrified."""
        facts = extractor.facts_for(record, signature=signature)
        race_name = scheme.resolve_race(facts, facts_for)
        if race_name is None or race_name in NON_FURRY_TARGETS:
            return False
        parent_race, breed = cust.resolve_race_or_breed(race_name)
        is_child = is_child_npc(extractor, record)
        furry_race = races.resolve(parent_race, is_child)
        if furry_race is None:
            return False
        if facts.race in ('GhoulRace', 'GhoulChildRace'):
            ghoul_targets[facts.race] = parent_race
        from .models import Sex
        sex = Sex.FEMALE if extractor.is_female(record) else Sex.MALE
        apply_furry(patch, record, furry_race, race_edid=parent_race, sex=sex,
                    signature=signature, headpart_pools=headpart_pools,
                    race_tints=race_tints, customization=cust,
                    breed_name=(breed.name if breed else None),
                    race_morphs=race_morphs, bone_regions=bone_regions,
                    minimal=is_child)
        if is_child:
            stats['minimal_children'] += 1
        stats['furrified'] += 1
        stats['race_counts'][parent_race] = \
            stats['race_counts'].get(parent_race, 0) + 1
        return True

    # Variant-expansion: clone-army templated NPCs (many placed actors funneling
    # through one face) get diversified into K furry variants behind a leveled
    # list. We inject at each leaf's CLOSEST traits template (the injection node),
    # NOT the deep owner — the engine ignores a Traits redirect on an actor it
    # selected from a leveled list, so a deep redirect silently falls back to the
    # template's race (the DC-guard cheetah bug). Precompute the plan from the
    # placed-actor scan; nodes that already offer enough distinct faces are left
    # alone. See variants.py.
    injections: dict = {}
    if variant_expansion:
        emit("Counting placed instances…")     # indeterminate (ACHR scan)
        injections = plan_injections(ps, winning, winning_lvln)
        log.info("variant-expansion: %d injection nodes "
                 "(%d total placed instances)", len(injections),
                 sum(p.instances for p in injections.values()))

    # object_index of every record handled (furrified or deliberately skipped),
    # so it isn't re-processed in the owner pass.
    done: set = set()
    # Trait-owners reached from furrified Use-Traits leaves whose chain is NOT
    # diversified by an injection — furrify these so those leaves inherit a furry
    # appearance through their (untouched) template chain.
    required_owners: set = set()

    # Injection pre-pass: at each planned node, mint K furry variants (copied from
    # a representative owner below it) and redirect that node's TPTA[Traits] slot
    # to the variant LVLN. Leaves through these nodes then roll a varied face at
    # spawn; their deep owners no longer need furrifying for them.
    if injections:
        emit("Injecting variant faces", 0, len(injections))
        for i, (node_obj, plan) in enumerate(injections.items()):
            _check_cancel(cancel_event)
            node = winning.get(node_obj)
            variant_base = winning.get(plan.variant_base)
            if node is None or variant_base is None:
                continue
            result = expand_at_node(patch, node, variant_base, plan.k,
                                    furrify_variant)
            done.add(node_obj)          # the node is overridden; don't reprocess
            if result is not None:
                stats['expanded_owners'] += 1
                stats['variants'] += len(result.variants)

    total_npcs = len(winning)
    emit("Furrifying NPCs", 0, total_npcs)
    for npc in winning.values():
        _check_cancel(cancel_event)
        stats['total'] += 1
        if stats['total'] % 64 == 0:
            emit("Furrifying NPCs", stats['total'], total_npcs)
        objid = npc.form_id.value & 0xFFFFFF
        if objid in done:               # injection node already overridden
            continue
        # Preserve mode: an NPC already furrified by an earlier run (its absolute
        # winner is furrifier output) is left untouched.
        if not refurrify_existing and objid in furrified:
            stats['preserved'] += 1
            continue
        if only_npc_set is not None and (npc.editor_id or '') not in only_npc_set:
            continue
        facts = facts_for(npc.editor_id or '') or extractor.facts_for(npc)
        if only_factions is not None and not (only_factions & facts.factions):
            continue
        # A Use-Traits leaf takes race + appearance from its template, so
        # furrifying the leaf is dead data + orphan facegen. If an injection
        # diversifies its chain (its injection node is planned), it's handled —
        # skip without collecting owners. Otherwise record its trait-owner(s) to
        # furrify; the leaf inherits through the (unchanged) chain.
        if is_templated_leaf(npc):
            stats['templated'] += 1
            node = traits_injection_node(npc, objid, winning, winning_lvln)
            if node not in injections:
                required_owners |= resolve_trait_owners(npc, winning, winning_lvln)
            continue
        # Mark processed whether or not it furrified — a non-templated NPC that
        # gates out here must not be re-attempted (and re-counted) in the owner
        # pass below.
        did_furrify = do_furrify(npc)
        done.add(objid)
        if did_furrify and limit is not None and stats['furrified'] >= limit:
            log.info("hit limit of %d", limit)
            break

    # Owner pass: furrify trait-owners reached from leaves but not already done.
    # Bypasses only_factions on purpose — a template usually carries no faction,
    # yet the in-faction leaf that points at it must still come out furry.
    if limit is None or stats['furrified'] < limit:
        emit("Furrifying trait-owners", 0, len(required_owners))
        for owner_obj in required_owners:
            _check_cancel(cancel_event)
            if owner_obj in done:
                continue
            owner = winning.get(owner_obj)
            if owner is None:
                continue
            if do_furrify(owner):
                done.add(owner_obj)
                stats['owner_furrified'] += 1
                if limit is not None and stats['furrified'] >= limit:
                    break

    # Ghoul armor pass: ghoul races have a non-human body, so armor addons
    # listing GhoulRace/GhoulChildRace need the furry target race added or
    # equipment won't render on furrified ghouls.
    from .armor import add_race_to_all_armor
    if ghoul_targets:
        emit("Fitting ghoul armor…")
    for ghoul_race_edid, target_name in ghoul_targets.items():
        ghoul_race = races.get(ghoul_race_edid)
        # The ARMA-fix target is the furry race actually assigned to ghouls;
        # use the child variant for GhoulChildRace when one exists.
        is_child = ghoul_race_edid == 'GhoulChildRace'
        target_race = races.resolve(target_name, is_child)
        if ghoul_race is None or target_race is None:
            continue
        stats['armas_patched'] += add_race_to_all_armor(
            patch, ps, ghoul_race, target_race)

    # Sort the master list into canonical order (master-flagged before ESP)
    # before saving. esplib adds masters in first-referenced order; FO4 requires
    # them sorted, and a light (ESL) master stranded in the ESP section makes the
    # engine/xEdit mis-resolve overrides of its records (they fall back to a
    # base-game FormID). Remaps every FormID to the new indices.
    if patch.sort_masters():
        log.info("sorted patch master list into canonical order")

    # Light (ESL/ESPFE) flag: requested via emit_esl, but only honored if the
    # run's NEW records fit the light object-ID range (≤ 2048). A larger run
    # falls back to a full ESP with a warning. Extension stays .esp either way.
    stats['new_records'] = esl_new_record_count(patch)
    if emit_esl:
        made_light, count = apply_esl_flag(patch)
        stats['esl'] = made_light
        if made_light:
            log.info("ESL: flagged patch light (%d new records, limit %d)",
                     count, ESL_MAX_NEW_RECORDS)
        else:
            log.warning("ESL requested but patch has %d new records (limit "
                        "%d); saving as a full ESP instead", count,
                        ESL_MAX_NEW_RECORDS)

    emit("Saving patch…")
    patch.save()
    log.info("saved %s: %d furrified / %d total (%d preserved); "
             "%d templated leaves -> %d trait-owners furrified; "
             "%d owners expanded into %d variants",
             patch_name, stats['furrified'], stats['total'], stats['preserved'],
             stats['templated'], stats['owner_furrified'],
             stats['expanded_owners'], stats['variants'])

    # Final pass: self-bake each furrified NPC's facegen — the FaceCustomization
    # texture set (diffuse with real tint-blend composition + copied normal/
    # specular) AND the facegeom nif (baked so the engine doesn't build geometry
    # at cell-load time and hitch). The patch holds only furrified NPCs, so this
    # naturally scopes to them. Reads assets from data_dir, writes to output_dir.
    if bake_facegen:
        from .facegen import build_facegen_for_patch
        # Reuse the world's facegen indexes (esp. the AssetResolver, whose BA2
        # scan is the expensive part) so the bake doesn't rebuild them. The world
        # owns the resolver, so build_facegen_for_patch leaves it open.
        stats['facegen'] = build_facegen_for_patch(
            patch, ps, str(data),
            fallback_dir=str(world.fallback) if world.fallback else None,
            output_dir=str(out),
            output_size=facegen_size, workers=workers, throttle=throttle,
            race_morphs=race_morphs, bone_regions=bone_regions,
            extractor=extractor, templates=world.tint_templates,
            pools=headpart_pools, races_by_edid=world.races_by_edid,
            resolver=world.resolver, base_heads=world.base_heads,
            progress=progress, cancel_event=cancel_event)

        # Pack the freshly-baked loose facegen into a pair of game-loadable
        # BA2s (Main GNRL .nif + Textures DX10 .dds) named after the patch, and
        # remove the loose trees. Run-only; needs the bake to have happened.
        if pack:
            from .pack import pack_facegen
            emit("Packing facegen into BA2…")
            archives = pack_facegen(out, patch_name)
            stats['packed'] = [str(p) for p in archives]

    emit("Done")
    # Release the world we built ourselves (CLI/tests). A caller-supplied world
    # (the GUI's) is left open — it owns its lifetime and reuses it.
    if own_world:
        world.close()
    return stats
