"""Unit tests for trait-template chain resolution (templates.py).

Pure: no esplib/game files. Synthetic NPC/LVLN records pin the behaviors the
memoize fix relies on — Use-Traits detection, chain-walk to trait owners,
LVLN fan-out, the DC-guard collapse (many entries -> one owner), nested
leveled lists, and cycle/dead-end safety.
"""

import struct

from furrifier_fo4.templates import (
    uses_traits, template_object, lvln_entry_objects, is_templated_leaf,
    resolve_trait_owners,
)


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


def _acbs(use_traits):
    d = bytearray(20)
    struct.pack_into("<H", d, 14, 0x0001 if use_traits else 0x0000)
    return _Sub("ACBS", bytes(d))


def _tplt(obj):
    return _Sub("TPLT", struct.pack("<I", obj))  # file_index 0 + object_index


def _lvlo(obj):
    d = bytearray(12)
    struct.pack_into("<I", d, 4, obj)
    return _Sub("LVLO", bytes(d))


def npc(use_traits=False, tplt=None):
    subs = [_acbs(use_traits)]
    if tplt is not None:
        subs.append(_tplt(tplt))
    return _Rec(*subs)


def lvln(*entry_objs):
    return _Rec(*[_lvlo(o) for o in entry_objs])


# ---------------------------------------------------------------- primitives --

def test_uses_traits_reads_template_flag_bit0():
    assert uses_traits(npc(use_traits=True)) is True
    assert uses_traits(npc(use_traits=False)) is False


def test_uses_traits_false_without_acbs():
    assert uses_traits(_Rec()) is False


def test_template_object_extracts_low_three_bytes():
    # file_index in the high byte must be stripped.
    assert template_object(npc(use_traits=True, tplt=0x06012345)) == 0x012345
    assert template_object(npc(use_traits=True)) is None


def test_lvln_entry_objects_strip_file_index():
    assert lvln_entry_objects(lvln(0x06000111, 0x01000222)) == [0x000111, 0x000222]


def test_is_templated_leaf_requires_both_flag_and_tplt():
    assert is_templated_leaf(npc(use_traits=True, tplt=0x10)) is True
    assert is_templated_leaf(npc(use_traits=True, tplt=None)) is False
    assert is_templated_leaf(npc(use_traits=False, tplt=0x10)) is False


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
