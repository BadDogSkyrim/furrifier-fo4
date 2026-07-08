"""Unit tests for trait-template chain resolution (templates.py).

Pure: no esplib/game files. Synthetic NPC/LVLN records pin the behaviors the
memoize fix relies on — Use-Traits detection, chain-walk to trait owners,
LVLN fan-out, the DC-guard collapse (many entries -> one owner), nested
leveled lists, and cycle/dead-end safety.
"""

import struct

from furrifier_fo4.templates import (
    uses_traits, template_object, tpta_traits_object, traits_template_object,
    lvln_entry_objects, is_templated_leaf, resolve_trait_owners,
    traits_injection_node,
)


class _Norm:
    def __init__(self, value):
        self.value = value


class _Sub:
    def __init__(self, signature, data):
        self.signature = signature
        self.data = data


class _Rec:
    def __init__(self, *subs):
        self._subs = list(subs)

    @property
    def subrecords(self):
        return self._subs

    def get_subrecord(self, sig):
        for s in self._subs:
            if s.signature == sig:
                return s
        return None

    def normalize_form_id(self, fid):
        # Identity: these synthetic records have no master list, so the raw
        # value already is the (here object-index-only) key.
        return _Norm(fid if isinstance(fid, int) else fid.value)


def _acbs(use_traits):
    d = bytearray(20)
    struct.pack_into("<H", d, 14, 0x0001 if use_traits else 0x0000)
    return _Sub("ACBS", bytes(d))


def _tplt(obj):
    return _Sub("TPLT", struct.pack("<I", obj))  # file_index 0 + object_index


def _tpta(traits_obj):
    # 13 per-category FormIDs (52 bytes); only the Traits slot (0) is set here.
    d = bytearray(52)
    struct.pack_into("<I", d, 0, traits_obj)
    return _Sub("TPTA", bytes(d))


def _lvlo(obj):
    d = bytearray(12)
    struct.pack_into("<I", d, 4, obj)
    return _Sub("LVLO", bytes(d))


def npc(use_traits=False, tplt=None, tpta_traits=None):
    subs = [_acbs(use_traits)]
    if tplt is not None:
        subs.append(_tplt(tplt))
    if tpta_traits is not None:
        subs.append(_tpta(tpta_traits))
    return _Rec(*subs)


def lvln(*entry_objs):
    return _Rec(*[_lvlo(o) for o in entry_objs])


# ---------------------------------------------------------------- primitives --

def test_uses_traits_reads_template_flag_bit0():
    assert uses_traits(npc(use_traits=True)) is True
    assert uses_traits(npc(use_traits=False)) is False


def test_uses_traits_false_without_acbs():
    assert uses_traits(_Rec()) is False


def test_template_object_normalizes_full_reference():
    # The whole TPLT FormID (file index included) is normalized through the
    # owning record — NOT masked to the object index, which collided records
    # across plugins. The identity fake returns the full value unchanged.
    assert template_object(npc(use_traits=True, tplt=0x06012345)) == 0x06012345
    assert template_object(npc(use_traits=True)) is None


def test_lvln_entry_objects_normalize_full_reference():
    assert lvln_entry_objects(lvln(0x06000111, 0x01000222)) == [0x06000111, 0x01000222]


def test_is_templated_leaf_requires_both_flag_and_tplt():
    assert is_templated_leaf(npc(use_traits=True, tplt=0x10)) is True
    assert is_templated_leaf(npc(use_traits=True, tplt=None)) is False
    assert is_templated_leaf(npc(use_traits=False, tplt=0x10)) is False


# ----------------------------------------------------------- injection node --

def test_injection_node_is_the_immediate_template_npc():
    # leaf -> LvlSec(NPC) -> LChar(LVLN) -> owners. The redirect must land on the
    # immediate NPC template (reached by a direct link), NOT a deep owner reached
    # through the leveled list (the DC-guard cheetah bug).
    leaf = npc(use_traits=True, tplt=0x200)            # 0x200 = LvlSec (an NPC)
    win_npc = {0x100: leaf, 0x200: npc(use_traits=True, tplt=0x300)}
    win_lvln = {0x300: lvln(0x10, 0x20)}               # LChar
    assert traits_injection_node(leaf, 0x100, win_npc, win_lvln) == 0x200


def test_injection_node_is_leaf_when_immediate_target_is_leveled():
    # leaf -> LVLN directly: no NPC template to carry the redirect, so the leaf
    # itself is the node.
    leaf = npc(use_traits=True, tplt=0x300)
    win_npc = {0x100: leaf}
    win_lvln = {0x300: lvln(0x10, 0x20)}
    assert traits_injection_node(leaf, 0x100, win_npc, win_lvln) == 0x100


def test_injection_node_follows_tpta_traits_over_tplt():
    # TPTA[Traits] wins over TPLT for the immediate target.
    leaf = npc(use_traits=True, tplt=0x999, tpta_traits=0x200)
    win_npc = {0x100: leaf, 0x200: npc(use_traits=False)}
    assert traits_injection_node(leaf, 0x100, win_npc, {}) == 0x200


def test_injection_node_none_without_template():
    leaf = npc(use_traits=True)
    assert traits_injection_node(leaf, 0x100, {0x100: leaf}, {}) is None


# ---------------------------------------------------- TPTA Traits override ----
# The engine resolves each template category from its own TPTA slot when set,
# falling back to TPLT only when the slot is null. The Traits slot (0) is the
# appearance/race source — Bethesda routinely points TPLT at a stats/combat
# template while a separate '…FaceAndRace' list drives Traits, so face/race
# resolution MUST follow TPTA[Traits], not bare TPLT.

def test_tpta_traits_object_extracts_slot0():
    # Slot 0 is normalized through the owning record (full ref, not masked).
    assert tpta_traits_object(npc(use_traits=True, tpta_traits=0x06012345)) == 0x06012345
    assert tpta_traits_object(npc(use_traits=True)) is None
    # A null Traits slot reads as absent, not as object 0.
    assert tpta_traits_object(npc(use_traits=True, tpta_traits=0x0)) is None


def test_traits_template_object_prefers_tpta_over_tplt():
    rec = npc(use_traits=True, tplt=0xAAA, tpta_traits=0xBBB)
    assert traits_template_object(rec) == 0xBBB     # TPTA wins
    assert template_object(rec) == 0xAAA            # raw TPLT still readable


def test_traits_template_object_falls_back_to_tplt_when_no_tpta():
    assert traits_template_object(npc(use_traits=True, tplt=0xAAA)) == 0xAAA


def test_is_templated_leaf_with_tpta_traits_and_no_tplt():
    # Appearance can come purely from the TPTA Traits slot, with no TPLT at all.
    assert is_templated_leaf(npc(use_traits=True, tpta_traits=0x10)) is True


def test_resolve_follows_tpta_traits_not_tplt():
    # Models LvlGoodneighborTriggermanWarehouse (0011A2F5): TPLT points at the
    # combat/stats list (wrong owner), TPTA[Traits] at the FaceAndRace list whose
    # entries are the real appearance owners. Resolution must return the latter.
    leaf = npc(use_traits=True, tplt=0x100, tpta_traits=0x200)
    win_lvln = {
        0x100: lvln(0x11),          # LCharTriggerman -> stats template (wrong)
        0x200: lvln(0x21, 0x22),    # LCharTriggermanFaceAndRace -> real owners
    }
    win_npc = {
        0x11: npc(False),           # EncTriggermanTemplate00 (stats owner)
        0x21: npc(False),           # EncTriggermanFaceM01
        0x22: npc(False),           # EncTriggermanGhoulFaceM01
    }
    owners = resolve_trait_owners(leaf, win_npc, win_lvln)
    assert owners == {0x21, 0x22}   # NOT {0x11}


def test_resolve_null_tpta_traits_slot_falls_back_to_tplt():
    # TPTA present but Traits slot null -> the engine uses TPLT for Traits.
    leaf = npc(use_traits=True, tplt=0x100, tpta_traits=0x0)
    owners = resolve_trait_owners(leaf, {0x100: npc(False)}, {})
    assert owners == {0x100}


# -------------------------------------------------------------- resolution ----

def test_leaf_to_single_owner_npc():
    owner = npc(use_traits=False)            # owns its race
    leaf = npc(use_traits=True, tplt=0x100)
    owners = resolve_trait_owners(leaf, {0x100: owner}, {})
    assert owners == {0x100}


def test_chain_through_intermediate_use_traits_npc():
    leaf = npc(use_traits=True, tplt=0x100)
    mid = npc(use_traits=True, tplt=0x200)   # forwards again
    owner = npc(use_traits=False)
    owners = resolve_trait_owners(
        leaf, {0x100: mid, 0x200: owner}, {})
    assert owners == {0x200}


def test_lvln_fans_out_to_distinct_owners():
    leaf = npc(use_traits=True, tplt=0x100)
    win_lvln = {0x100: lvln(0x10, 0x20, 0x30)}
    win_npc = {0x10: npc(False), 0x20: npc(False), 0x30: npc(False)}
    owners = resolve_trait_owners(leaf, win_npc, win_lvln)
    assert owners == {0x10, 0x20, 0x30}


def test_dc_guard_collapse_many_entries_one_owner():
    # Entries all forward (Use-Traits) to the SAME owner -> variety ceiling 1.
    leaf = npc(use_traits=True, tplt=0x100)
    win_lvln = {0x100: lvln(0x11, 0x12, 0x13)}
    win_npc = {
        0x11: npc(True, tplt=0x99),
        0x12: npc(True, tplt=0x99),
        0x13: npc(True, tplt=0x99),
        0x99: npc(False),
    }
    owners = resolve_trait_owners(leaf, win_npc, win_lvln)
    assert owners == {0x99}


def test_nested_lvln_of_lvln():
    leaf = npc(use_traits=True, tplt=0xA0)
    win_lvln = {0xA0: lvln(0xB0, 0x10), 0xB0: lvln(0x20, 0x30)}
    win_npc = {0x10: npc(False), 0x20: npc(False), 0x30: npc(False)}
    owners = resolve_trait_owners(leaf, win_npc, win_lvln)
    assert owners == {0x10, 0x20, 0x30}


def test_cycle_is_guarded_and_yields_no_owner():
    # A -> B -> A, both Use-Traits: no owner, must terminate.
    leaf = npc(use_traits=True, tplt=0xA)
    win_npc = {0xA: npc(True, tplt=0xB), 0xB: npc(True, tplt=0xA)}
    assert resolve_trait_owners(leaf, win_npc, {}) == set()


def test_dead_end_missing_template_yields_no_owner():
    leaf = npc(use_traits=True, tplt=0x500)
    assert resolve_trait_owners(leaf, {}, {}) == set()
