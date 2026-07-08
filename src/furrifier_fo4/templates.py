"""Resolve FO4 NPC trait-template chains down to their trait-*owner* records.

A "Use Traits" NPC (ACBS template_flags bit 0) takes its race and appearance
from its `TPLT` target, not from its own record — so furrifying the leaf writes
dead data the engine ignores, and bakes orphan facegen. This module walks the
`TPLT`/`LVLN` chain to the trait *owner(s)*: the first NPC in each branch that
does NOT use traits, i.e. the record that actually carries the race + head.

The furrifier furrifies those owners (once each — they are heavily shared; e.g.
one `EncRaider01Template` backs ~290 leaves) and skips the leaves, which then
inherit the furry appearance through the unchanged template chain.

Records are keyed throughout by their load-order-normalized FormID (so records
from different plugins that share an object index stay distinct), matching
`world.build_winning`'s winning-record maps. Each helper normalizes the FormIDs
it reads through the *owning* record, so the keys it returns are directly
comparable against those maps.
"""

from __future__ import annotations

import struct
from typing import Optional

# ACBS struct: flags(u32) xp(s16) level(u16) calc_min(u16) calc_max(u16)
# disposition(s16) template_flags(u16) ... — template_flags begins at byte 14.
_TEMPLATE_FLAGS_OFFSET = 14
_USE_TRAITS = 0x0001

# TPTA ("Template Actors") = 13 per-category template FormIDs (BMMO/LVLN/NPC_/
# NULL), 4 bytes each. The engine resolves each template category from its own
# TPTA slot if that slot is non-null, falling back to the generic TPLT only when
# it is null. Traits (race + head + appearance) is slot 0, byte offset 0.
_TPTA_TRAITS_OFFSET = 0


def _sig(subrecord) -> str:
    s = subrecord.signature
    return s.decode("ascii", "replace") if isinstance(s, (bytes, bytearray)) else s


def uses_traits(npc) -> bool:
    """True if `npc`'s ACBS has the Use-Traits template flag set (race +
    appearance come from its template, not this record)."""
    acbs = npc.get_subrecord("ACBS")
    if acbs is None:
        return False
    data = acbs.data
    if len(data) < _TEMPLATE_FLAGS_OFFSET + 2:
        return False
    flags = struct.unpack_from("<H", data, _TEMPLATE_FLAGS_OFFSET)[0]
    return bool(flags & _USE_TRAITS)


# ACBS "flags" is the u32 at byte 0 (distinct from the u16 template_flags at
# byte 14). Bit 5 = Unique.
_ACBS_UNIQUE = 0x00000020


def is_unique(npc) -> bool:
    """True if the NPC's ACBS Unique flag is set. A unique (one-off) character
    has a fixed identity, so it must never be diversified into variant faces —
    it's furrified in place instead."""
    acbs = npc.get_subrecord("ACBS")
    if acbs is None or len(acbs.data) < 4:
        return False
    return bool(struct.unpack_from("<I", acbs.data, 0)[0] & _ACBS_UNIQUE)


def template_object(npc) -> Optional[int]:
    """Record key of the NPC's Default Template (TPLT), or None if absent."""
    sr = npc.get_subrecord("TPLT")
    if sr is None or len(sr.data) < 4:
        return None
    raw = int.from_bytes(sr.data[:4], "little")
    return npc.normalize_form_id(raw).value if raw else None


def tpta_traits_object(npc) -> Optional[int]:
    """Record key of the NPC's per-category Traits template (TPTA slot 0), or
    None if there's no TPTA or that slot is null."""
    sr = npc.get_subrecord("TPTA")
    if sr is None or len(sr.data) < _TPTA_TRAITS_OFFSET + 4:
        return None
    raw = int.from_bytes(
        sr.data[_TPTA_TRAITS_OFFSET:_TPTA_TRAITS_OFFSET + 4], "little")
    return npc.normalize_form_id(raw).value if raw else None


def traits_template_object(npc) -> Optional[int]:
    """Record key of the template the engine takes this NPC's Traits (race +
    head + appearance) from: the TPTA Traits slot if non-null, else the generic
    TPLT fallback. This — not bare TPLT — is the appearance source, so all face/
    race resolution must follow it (Bethesda routinely points TPLT at a combat/
    stats template while a separate '…FaceAndRace' list drives Traits)."""
    return tpta_traits_object(npc) or template_object(npc)


def lvln_entry_objects(lvln) -> list:
    """Record keys of an LVLN's LVLO leveled-list entries (the FormID sits at
    byte 4 of each 12-byte LVLO)."""
    out = []
    for sr in lvln.subrecords:
        if _sig(sr) == "LVLO" and len(sr.data) >= 8:
            raw = int.from_bytes(sr.data[4:8], "little")
            if raw:
                out.append(lvln.normalize_form_id(raw).value)
    return out


def is_templated_leaf(npc) -> bool:
    """True if `npc` defers its traits to a template (Use-Traits + a resolvable
    Traits template). A Use-Traits NPC can drive appearance purely through its
    TPTA Traits slot with no TPLT at all, so check the traits-aware target."""
    return uses_traits(npc) and traits_template_object(npc) is not None


def traits_injection_node(leaf, leaf_obj: int, winning_npc: dict,
                          winning_lvln: dict) -> Optional[int]:
    """object_index of the node whose ``TPTA[Traits]`` slot to redirect when
    injecting appearance variety for `leaf`.

    The engine honors a Traits redirect on a template reached by a DIRECT
    TPLT/TPTA link, but NOT on an actor it selected from a leveled list — so we
    redirect as close to the leaf as possible, before any leveled hop. That is
    the leaf's IMMEDIATE Traits template if it's an NPC; if the immediate target
    is a leveled list (no NPC there to carry the redirect), the leaf itself is
    the node. Returns None if the leaf has no Traits template at all.

    (Redirecting the deep trait-OWNER instead fails when that owner is reached
    through a leveled list — the chain falls back to its TPLT, e.g. cheetah.)
    """
    t = traits_template_object(leaf)
    if t is None:
        return None
    if t in winning_lvln:          # leaf points straight at a leveled list
        return leaf_obj
    if t in winning_npc:           # redirect the immediate template NPC (direct)
        return t
    return None


def resolve_trait_owners(npc, winning_npc: dict, winning_lvln: dict) -> set:
    """The set of trait-owner object_indexes `npc` resolves to.

    Walks `npc`'s Traits template (TPTA Traits slot, else TPLT) through
    Use-Traits NPC templates and LVLN entries, collecting every NPC that does
    NOT use traits — the records that actually own race + appearance. Returns an
    empty set for a dead-end chain (a Use-Traits NPC whose template is missing or
    itself dead-ends). Shared sub-chains are walked once; cycles are guarded.
    """
    owners: set = set()
    seen: set = set()

    def dispatch(obj: int) -> None:
        # LVLN wins on the rare object_index collision — a TPLT/LVLO pointing at
        # a leveled list is unambiguous in practice.
        if obj in winning_lvln:
            walk("lvln", obj)
        elif obj in winning_npc:
            walk("npc", obj)

    def walk(kind: str, obj: int) -> None:
        key = (kind, obj)
        if key in seen:
            return
        seen.add(key)
        if kind == "lvln":
            lvln = winning_lvln.get(obj)
            if lvln is None:
                return
            for entry in lvln_entry_objects(lvln):
                dispatch(entry)
        else:
            sub = winning_npc.get(obj)
            if sub is None:
                return
            if uses_traits(sub):
                t = traits_template_object(sub)
                if t is not None:
                    dispatch(t)
            else:
                owners.add(obj)

    start = traits_template_object(npc)
    if start is not None:
        dispatch(start)
    return owners
