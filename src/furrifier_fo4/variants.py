"""Variant expansion: diversify clone-army trait-owners.

A handful of trait-owner templates back most of FO4's NPCs — one
`EncRaider01Template` behind ~290 raider leaves, two `EncSecurityDiamondCity`
templates behind every Diamond City guard. The memoize pass furrifies each such
owner ONCE, so every raider / guard shows the same furry face. For heavily-used
owners this module replaces that single furrification with K appearance
**variants**:

  - mint K furrified copies of the owner on distinct signatures — each rolls its
    own species + markings (standard furrification),
  - collect them in a fresh `LVLN` (leveled NPC list),
  - point the owner's `TPTA[Traits]` slot at that LVLN and set its Use-Traits
    flag.

The engine then rolls the LVLN per actor-instance at spawn, so the clone army
becomes a varied crowd — WITHOUT touching the existing leveled lists that
distribute the NPCs, and with level scaling intact (only Traits redirect).

K scales with `instances(R)` — how many placed actors actually resolve to the
owner — capped. See PLAN_FO4_VARIANT_EXPANSION.md. The tunables below are the
only knobs; they are intentionally not exposed as config.
"""

from __future__ import annotations

import logging
import struct

from .templates import is_templated_leaf, resolve_trait_owners

log = logging.getLogger(__name__)

# -- Tunables (internal; adjust here, no user-facing config) ----------------
EXPAND_THRESHOLD = 3       # min placed instances before an owner is diversified
_K_FACTOR = 1.3            # birthday-paradox headroom over the instance count
_K_MIN = 8
_K_MAX = 24

# -- FO4 record layout constants --------------------------------------------
_USE_TRAITS = 0x0001
_ACBS_TEMPLATE_FLAGS_OFFSET = 14   # u16 template_flags within ACBS
_TPTA_SIZE = 52                    # 13 FormIDs; Traits is slot 0 (byte offset 0)
_LVLF_ALL_LEVELS = 0x01            # "calculate from all levels <= player's level"


def variant_count(instances: int) -> int:
    """K for an owner seen `instances` times: clamp(round(1.3*instances), 8, 24)."""
    return max(_K_MIN, min(_K_MAX, round(_K_FACTOR * instances)))


def count_instances(plugin_set, winning_npc: dict, winning_lvln: dict,
                    owner_set: set) -> dict:
    """Tally placed-actor instances per trait-owner.

    For every placed ACHR, resolve its base NPC to the trait-owner(s) the engine
    would render (the template chain for a Use-Traits leaf, else the base
    itself) and count toward each owner that is in `owner_set` (the records that
    are actually trait-owners of some leaf — the safe-to-expand set). Returns
    {owner_object_index: count}. Memoized per base, so the per-ACHR cost is a
    dict lookup once each distinct base is resolved.
    """
    counts: dict = {}
    cache: dict = {}

    def effective(base_obj: int):
        hit = cache.get(base_obj)
        if hit is not None:
            return hit
        base = winning_npc.get(base_obj)
        if base is None:
            res = ()
        elif is_templated_leaf(base):
            res = tuple(resolve_trait_owners(base, winning_npc, winning_lvln))
        else:
            res = (base_obj,)
        cache[base_obj] = res
        return res

    for plugin in plugin_set:
        for achr in plugin.get_records_by_signature('ACHR'):
            name = achr.get_subrecord('NAME')
            if name is None or len(name.data) < 4:
                continue
            base_obj = int.from_bytes(name.data[:4], 'little') & 0xFFFFFF
            for owner in effective(base_obj):
                if owner in owner_set:
                    counts[owner] = counts.get(owner, 0) + 1
    return counts


class ExpansionResult:
    __slots__ = ("owner_override", "lvln", "variants")

    def __init__(self, owner_override, lvln, variants):
        self.owner_override = owner_override
        self.lvln = lvln
        self.variants = variants


def expand_owner(patch, owner_base, k: int, furrify_variant):
    """Diversify `owner_base` into up to `k` appearance variants.

    `furrify_variant(record, signature) -> bool` resolves a race on `signature`
    and applies the furry appearance to `record` in place, returning True if it
    furrified (False if that roll gated to non-furry — the variant is dropped).

    Mints the furrified variants (fresh FormIDs, distinct EDIDs/signatures →
    distinct species), builds an LVLN of them, and creates an owner override
    that defers Traits to the LVLN (Use-Traits flag + TPTA[Traits] slot). The
    existing leveled lists that feed the owner are untouched.

    Returns an `ExpansionResult`, or None if no variant furrified (caller falls
    back to furrifying the owner in place).
    """
    base_edid = owner_base.editor_id or f"{owner_base.form_id.value & 0xFFFFFF:06X}"
    variants = []
    for i in range(k):
        v = patch.copy_record(owner_base, owner_base.plugin, new_form_id=True)
        v.editor_id = f"{base_edid}_F{i:02d}"
        if furrify_variant(v, v.editor_id):
            variants.append(v)
        else:
            patch.remove_record(v)   # this roll landed non-furry; drop it
    if not variants:
        return None

    lvln = _build_variant_lvln(patch, base_edid, variants)
    owner_ov = patch.copy_record(owner_base, owner_base.plugin)  # override
    _redirect_traits_to_lvln(patch, owner_ov, lvln)
    return ExpansionResult(owner_ov, lvln, variants)


def _build_variant_lvln(patch, base_edid: str, variants: list):
    """A leveled-NPC list of `variants`, all at level 1 / count 1 so the engine
    picks one uniformly per spawn. Subrecords are added in canonical order
    (LVLN is unschematized, so insertion order is preserved)."""
    lvln = patch.new_record('LVLN', edid=f"{base_edid}_FurryVariants")
    lvln.add_subrecord('OBND', bytes(12))
    lvln.add_subrecord('LVLD', bytes(1))                 # chance none = 0
    lvln.add_subrecord('LVLM', bytes(1))                 # max count = 0
    lvln.add_subrecord('LVLF', bytes([_LVLF_ALL_LEVELS]))
    lvln.add_subrecord('LLCT', bytes([len(variants)]))
    for v in variants:
        entry = bytearray(12)
        struct.pack_into('<I', entry, 0, 1)              # level
        struct.pack_into('<I', entry, 8, 1)              # count
        lvlo = lvln.add_subrecord('LVLO', bytes(entry))
        patch.write_form_id(lvlo, 4, v.form_id)          # reference (sentinel+fixup)
    return lvln


def _redirect_traits_to_lvln(patch, owner_ov, lvln) -> None:
    """On the owner override: set the Use-Traits flag and point the TPTA Traits
    slot (slot 0) at `lvln`, so the engine takes race+appearance from a rolled
    variant. Other template categories / the owner's own TPLT are left intact."""
    acbs = owner_ov.get_subrecord('ACBS')
    if acbs is not None and len(acbs.data) >= _ACBS_TEMPLATE_FLAGS_OFFSET + 2:
        d = bytearray(acbs.data)
        flags = struct.unpack_from('<H', d, _ACBS_TEMPLATE_FLAGS_OFFSET)[0]
        struct.pack_into('<H', d, _ACBS_TEMPLATE_FLAGS_OFFSET, flags | _USE_TRAITS)
        acbs.data = d
        acbs.modified = True

    tpta = owner_ov.get_subrecord('TPTA')
    if tpta is None:
        tpta = owner_ov.add_subrecord('TPTA', bytes(_TPTA_SIZE))
    elif len(tpta.data) < _TPTA_SIZE:
        tpta.data = bytearray(tpta.data) + bytearray(_TPTA_SIZE - len(tpta.data))
    patch.write_form_id(tpta, 0, lvln.form_id)           # Traits slot
