"""Unit tests for variant-expansion sizing + the instance scan (variants.py).

Pure: synthetic records, no game files. The minting / LVLN / TPTA wiring is
exercised against live data in the integration validation, not here (it needs a
real patch + esplib FormID fixups).
"""

import struct

from furrifier_fo4.variants import variant_count, count_instances


def test_variant_count_clamps_and_scales():
    assert variant_count(1) == 8       # round(1.3)=1 -> Kmin
    assert variant_count(2) == 8       # round(2.6)=3 -> Kmin
    assert variant_count(8) == 10      # round(10.4)=10
    assert variant_count(15) == 20     # round(19.5)=20
    assert variant_count(20) == 24     # round(26)=26 -> Kmax
    assert variant_count(290) == 24    # cap


# --- count_instances with fakes -------------------------------------------

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


class _Plugin:
    def __init__(self, achrs):
        self._achrs = achrs

    def get_records_by_signature(self, sig):
        return iter(self._achrs) if sig == 'ACHR' else iter(())


def _acbs(use_traits):
    d = bytearray(20)
    struct.pack_into('<H', d, 14, 0x0001 if use_traits else 0)
    return _Sub('ACBS', bytes(d))


def _npc(use_traits=False, tplt=None):
    subs = [_acbs(use_traits)]
    if tplt is not None:
        subs.append(_Sub('TPLT', struct.pack('<I', tplt)))
    return _Rec(*subs)


def _lvln(*objs):
    return _Rec(*[_Sub('LVLO', bytearray(4) + struct.pack('<I', o) + bytearray(4))
                  for o in objs])


def _achr(base_obj):
    return _Rec(_Sub('NAME', struct.pack('<I', base_obj)))


def test_count_instances_tallies_through_template_to_owner():
    # Two ACHRs placed as a Use-Traits leaf that resolves to one owner.
    leaf = _npc(use_traits=True, tplt=0x200)
    owner = _npc(use_traits=False)
    win_npc = {0x100: leaf, 0x200: owner}
    ps = [_Plugin([_achr(0x100), _achr(0x100)])]
    counts = count_instances(ps, win_npc, {}, owner_set={0x200})
    assert counts == {0x200: 2}


def test_count_instances_direct_owner_placement_counts():
    owner = _npc(use_traits=False)
    win_npc = {0x200: owner}
    ps = [_Plugin([_achr(0x200), _achr(0x200), _achr(0x200)])]
    counts = count_instances(ps, win_npc, {}, owner_set={0x200})
    assert counts == {0x200: 3}


def test_count_instances_ignores_bases_not_in_owner_set():
    # A placed NPC that isn't a tracked trait-owner contributes nothing.
    win_npc = {0x300: _npc(use_traits=False)}
    ps = [_Plugin([_achr(0x300), _achr(0x999)])]   # 0x999 unknown base
    counts = count_instances(ps, win_npc, {}, owner_set={0x200})
    assert counts == {}


def test_count_instances_lvln_fanout_counts_each_owner():
    leaf_obj = 0x500
    leaf = _npc(use_traits=True, tplt=0x100)      # leaf -> LVLN -> two owners
    win_lvln = {0x100: _lvln(0x10, 0x20)}
    win_npc = {leaf_obj: leaf, 0x10: _npc(False), 0x20: _npc(False)}
    ps = [_Plugin([_achr(leaf_obj), _achr(leaf_obj)])]
    counts = count_instances(ps, win_npc, win_lvln, owner_set={0x10, 0x20})
    assert counts == {0x10: 2, 0x20: 2}
