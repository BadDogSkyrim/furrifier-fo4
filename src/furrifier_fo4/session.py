"""Run a furrification pass: load → resolve race per NPC → write patch.

This is the orchestration layer. It builds the load order, resolves each NPC's
furry race via the scheme engine (G1), extracts the facts it needs (G2), and
writes furrified overrides (G3 furrify_npc) into a new patch plugin.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from esplib import LoadOrder, PluginSet, Plugin, find_game_data, find_strings_dir

from .extract import FactExtractor
from .furrify import RaceLibrary, furrify_npc, apply_furry, is_child_npc
from .loader import load_scheme
from .models import (
    FURRIFIABLE_RACES, FURRIFIER_AUTHOR, NON_FURRY_TARGETS, is_furrifier_plugin,
)
from .templates import is_templated_leaf, resolve_trait_owners
from .variants import (
    count_instances, variant_count, expand_owner, EXPAND_THRESHOLD,
)

log = logging.getLogger(__name__)


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
        output_dir: Optional[str] = None,
        bake_facegen: bool = True,
        facegen_size: Optional[int] = 1024,
        refurrify_existing: bool = True,
        variant_expansion: bool = True,
        workers: Optional[int] = None,
        throttle: bool = False) -> dict:
    """Furrify the load order with `scheme_name`, writing `patch_name`.

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
    from .customization import load_customization
    data = Path(data_dir or find_game_data('fo4'))
    # Where to WRITE (patch + FaceGenData). Defaults to the read dir; point at
    # a mod-manager staging folder to keep the live Data tree clean.
    out = Path(output_dir) if output_dir else data
    scheme = load_scheme(scheme_name)
    scheme.build_indexes()

    if races_dir is None:
        races_dir = Path(__file__).resolve().parents[2] / 'races'
    cust = load_customization(Path(races_dir))

    if plugins is None:
        # Only the ENABLED plugins (active_only) — without it, plugins.txt's
        # inactive entries load too, furrifying NPCs from mods the user has
        # turned off. LoadOrder iterates plugin-name strings, not objects.
        plugins = list(LoadOrder.from_game('fo4', active_only=True))
    # Never furrify against our own output: a previously-saved patch left in
    # the load order would make every NPC resolve to its already-assigned furry
    # race, which fails the Human*/Ghoul* candidate gate -> 0 furrified.
    plugins = [p for p in plugins if p.lower() != patch_name.lower()]
    # Other furrifier-output plugins (by TES4 author, any filename) in the load
    # order make their NPCs "already furry". When re-furrifying, drop them so
    # everyone resolves from their vanilla base; when preserving, keep them
    # loaded and skip their NPCs in the loop below.
    furrifier_names = furrifier_output_names(plugins, data)
    if refurrify_existing and furrifier_names:
        plugins = [p for p in plugins if p.lower() not in furrifier_names]
        log.info("re-furrify: dropped %d prior furrifier output(s) from the "
                 "load order", len(furrifier_names))
    lo = LoadOrder.from_list(plugins, data_dir=str(data))
    ps = PluginSet(lo)
    strings = find_strings_dir('fo4')
    for p in ps:
        p.string_search_dirs = [str(strings)] if strings else []
    ps.load_all()
    log.info("loaded %d plugins", len(list(ps)))

    extractor = FactExtractor(ps)
    races = RaceLibrary(ps, child_races=cust.child_races)
    from .headparts import HeadpartPools
    headpart_pools = HeadpartPools(ps)
    from .tints import RaceTints
    race_tints = RaceTints(ps)

    patch = Plugin.new_plugin(str(out / patch_name), masters=[], game='fo4')
    patch.header.author = FURRIFIER_AUTHOR
    # Join the patch to the plugin set so write_form_id / copy_record can
    # denormalize load-order-indexed FormIDs (e.g. a furry race at load-order
    # index 7) into the patch's own master-list space. Without this the high
    # byte leaks through and RNAM/WNAM point at a nonexistent master index.
    patch.plugin_set = ps

    # Winning NPC / LVLN per object id (last in load order wins). The LVLN map
    # lets the template walk resolve leveled-actor template targets.
    winning: dict[int, Record] = {}
    winning_lvln: dict[int, Record] = {}
    for plugin in ps:
        for npc in plugin.get_records_by_signature('NPC_'):
            winning[npc.form_id.value & 0xFFFFFF] = npc
        for lvln in plugin.get_records_by_signature('LVLN'):
            winning_lvln[lvln.form_id.value & 0xFFFFFF] = lvln

    stats = {'total': 0, 'gated': 0, 'furrified': 0, 'left_human': 0,
             'no_child_race': 0, 'preserved': 0, 'armas_patched': 0,
             'templated': 0, 'owner_furrified': 0,
             'expanded_owners': 0, 'variants': 0, 'race_counts': {}}
    # ghoul vanilla race EDID -> furry target race name, filled during the run.
    ghoul_targets: dict[str, str] = {}

    # facts_lookup for family-leader resolution.
    npc_by_edid: dict[str, Record] = {}
    for npc in winning.values():
        if npc.editor_id:
            npc_by_edid[npc.editor_id] = npc
    facts_cache: dict[str, object] = {}

    def facts_for(edid):
        if edid not in facts_cache:
            n = npc_by_edid.get(edid)
            facts_cache[edid] = (
                extractor.facts_for(n, signature=scheme.signature_for(edid))
                if n is not None else None)
        return facts_cache[edid]

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
                    headpart_pools=headpart_pools, race_tints=race_tints,
                    customization=cust,
                    breed_name=(breed.name if breed else None))
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
                    breed_name=(breed.name if breed else None))
        stats['furrified'] += 1
        stats['race_counts'][parent_race] = \
            stats['race_counts'].get(parent_race, 0) + 1
        return True

    # Variant-expansion: clone-army trait-owners (many placed actors resolving
    # to one face) get diversified into K furry variants behind a leveled list,
    # instead of a single in-place furrification. Precompute which owners and how
    # many variants, from the placed-actor instance count. See variants.py.
    expand_K: dict = {}
    if variant_expansion:
        all_leaf_owners: set = set()
        for npc in winning.values():
            if is_templated_leaf(npc):
                all_leaf_owners |= resolve_trait_owners(npc, winning, winning_lvln)
        instances = count_instances(ps, winning, winning_lvln, all_leaf_owners)
        expand_K = {o: variant_count(instances[o]) for o in all_leaf_owners
                    if instances.get(o, 0) >= EXPAND_THRESHOLD}
        log.info("variant-expansion: %d trait-owners, %d above threshold "
                 "(%d placed instances counted)",
                 len(all_leaf_owners), len(expand_K), sum(instances.values()))

    def furrify_or_expand(npc) -> bool:
        """Diversify `npc` into K variants if it's a clone-army owner, else
        furrify it in place. Returns True if anything furrified."""
        obj = npc.form_id.value & 0xFFFFFF
        k = expand_K.get(obj)
        if k is not None:
            result = expand_owner(patch, npc, k, furrify_variant)
            if result is not None:
                stats['expanded_owners'] += 1
                stats['variants'] += len(result.variants)
                return True
            # No variant furrified (all rolls gated) — fall back to in-place.
        return do_furrify(npc)

    # object_index of every furrified record, so a trait-owner already done in
    # the main pass isn't re-furrified in the owner pass.
    done: set = set()
    # Trait-owners reached from furrified Use-Traits leaves — furrify these so
    # the leaves inherit a furry appearance through their (untouched) template
    # chain. A heavily-shared owner (e.g. EncRaider01Template) is collected once.
    required_owners: set = set()

    for npc in winning.values():
        stats['total'] += 1
        # Preserve mode: an NPC whose winning override is itself furrifier
        # output was already done by an earlier run — leave it untouched.
        if not refurrify_existing and is_furrifier_plugin(npc.plugin):
            stats['preserved'] += 1
            continue
        facts = facts_for(npc.editor_id or '') or extractor.facts_for(npc)
        if only_factions is not None and not (only_factions & facts.factions):
            continue
        # A Use-Traits leaf takes race + appearance from its template, so
        # furrifying the leaf is dead data + orphan facegen. Record its trait-
        # owner(s) to furrify instead; the leaf inherits through the chain.
        if is_templated_leaf(npc):
            stats['templated'] += 1
            required_owners |= resolve_trait_owners(npc, winning, winning_lvln)
            continue
        # Mark processed whether or not it furrified — a non-templated NPC that
        # gates out here must not be re-attempted (and re-counted) in the owner
        # pass below.
        furrified = furrify_or_expand(npc)
        done.add(npc.form_id.value & 0xFFFFFF)
        if furrified and limit is not None and stats['furrified'] >= limit:
            log.info("hit limit of %d", limit)
            break

    # Owner pass: furrify trait-owners reached from leaves but not already done.
    # Bypasses only_factions on purpose — a template usually carries no faction,
    # yet the in-faction leaf that points at it must still come out furry.
    if limit is None or stats['furrified'] < limit:
        for owner_obj in required_owners:
            if owner_obj in done:
                continue
            owner = winning.get(owner_obj)
            if owner is None:
                continue
            if furrify_or_expand(owner):
                done.add(owner_obj)
                stats['owner_furrified'] += 1
                if limit is not None and stats['furrified'] >= limit:
                    break

    # Ghoul armor pass: ghoul races have a non-human body, so armor addons
    # listing GhoulRace/GhoulChildRace need the furry target race added or
    # equipment won't render on furrified ghouls.
    from .armor import add_race_to_all_armor
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
        stats['facegen'] = build_facegen_for_patch(
            patch, ps, str(data), output_dir=str(out),
            output_size=facegen_size, workers=workers, throttle=throttle)

    return stats
