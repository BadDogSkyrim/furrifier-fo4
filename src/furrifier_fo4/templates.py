"""Resolve FO4 NPC trait-template chains down to their trait-*owner* records.

A "Use Traits" NPC (ACBS template_flags bit 0) takes its race and appearance
from its `TPLT` target, not from its own record — so furrifying the leaf writes
dead data the engine ignores, and bakes orphan facegen. This module walks the
`TPLT`/`LVLN` chain to the trait *owner(s)*: the first NPC in each branch that
does NOT use traits, i.e. the record that actually carries the race + head.

The furrifier furrifies those owners (once each — they are heavily shared; e.g.
one `EncRaider01Template` backs ~290 leaves) and skips the leaves, which then
inherit the furry appearance through the unchanged template chain.

Records are keyed throughout by `object_index` (the low 3 bytes of a FormID),
matching `session.run`'s winning-record maps.
"""

from __future__ import annotations

import struct
from typing import Optional

# ACBS struct: flags(u32) xp(s16) level(u16) calc_min(u16) calc_max(u16)
# disposition(s16) template_flags(u16) ... — template_flags begins at byte 14.
_TEMPLATE_FLAGS_OFFSET = 14
_USE_TRAITS = 0x0001


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


def template_object(npc) -> Optional[int]:
    """object_index of the NPC's Default Template (TPLT), or None if absent."""
    sr = npc.get_subrecord("TPLT")
    if sr is None or len(sr.data) < 4:
        return None
    return int.from_bytes(sr.data[:4], "little") & 0xFFFFFF


def lvln_entry_objects(lvln) -> list:
    """object_indexes of an LVLN's LVLO leveled-list entries (the FormID sits at
    byte 4 of each 12-byte LVLO)."""
    out = []
    for sr in lvln.subrecords:
        if _sig(sr) == "LVLO" and len(sr.data) >= 8:
            out.append(int.from_bytes(sr.data[4:8], "little") & 0xFFFFFF)
    return out


def is_templated_leaf(npc) -> bool:
    """True if `npc` defers its traits to a template (Use-Traits + has a TPLT)."""
    return uses_traits(npc) and template_object(npc) is not None


def resolve_trait_owners(npc, winning_npc: dict, winning_lvln: dict) -> set:
    """The set of trait-owner object_indexes `npc` resolves to.

    Walks `npc`'s `TPLT` target through Use-Traits NPC templates and LVLN
    entries, collecting every NPC that does NOT use traits — the records that
    actually own race + appearance. Returns an empty set for a dead-end chain
    (a Use-Traits NPC whose template is missing or itself dead-ends). Shared
    sub-chains are walked once; cycles are guarded.
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
                t = template_object(sub)
                if t is not None:
                    dispatch(t)
            else:
                owners.add(obj)

    start = template_object(npc)
    if start is not None:
        dispatch(start)
    return owners
