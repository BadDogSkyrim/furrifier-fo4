"""Unit tests for variant-expansion sizing + injection planning (variants.py).

Pure: synthetic records, no game files. The minting / LVLN / TPTA wiring is
exercised against live data in the integration validation, not here (it needs a
real patch + esplib FormID fixups).
"""

import struct

from furrifier_fo4.variants import (
    variant_count, plan_injections, SUFFICIENT_FACES, EXPAND_THRESHOLD,
)


def test_variant_count_clamps_and_scales():
    assert variant_count(1) == 8       # round(1.3)=1 -> Kmin
    assert variant_count(2) == 8       # round(2.6)=3 -> Kmin
    assert variant_count(8) == 10      # round(10.4)=10
    assert variant_count(15) == 20     # round(19.5)=20
    assert variant_count(20) == 24     # round(26)=26 -> Kmax
    assert variant_count(290) == 24    # cap


# --- fakes ----------------------------------------------------------------

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


def _npc(use_traits=False, tplt=None, tpta_traits=None):
    subs = [_acbs(use_traits)]
    if tplt is not None:
        subs.append(_Sub('TPLT', struct.pack('<I', tplt)))
    if tpta_traits is not None:
        d = bytearray(52)
        struct.pack_into('<I', d, 0, tpta_traits)
        subs.append(_Sub('TPTA', bytes(d)))
    return _Rec(*subs)


def _lvln(*objs):
    return _Rec(*[_Sub('LVLO', bytearray(4) + struct.pack('<I', o) + bytearray(4))
                  for o in objs])


def _achr(base_obj):
    return _Rec(_Sub('NAME', struct.pack('<I', base_obj)))


# --- plan_injections ------------------------------------------------------

def test_plan_injects_at_closest_template_not_deep_owner():
    # DC-guard topology: leaf -> LvlSec(NPC) -> LChar(LVLN) -> {M01, M02}.
    # The redirect must land on the immediate NPC template (0x200), reached by a
    # direct link, NOT the deep owners behind the leveled list.
    leaf, lvlsec = 0x100, 0x200
    lchar = 0x300
    win_npc = {
        leaf: _npc(use_traits=True, tplt=lvlsec),
        lvlsec: _npc(use_traits=True, tplt=lchar),
        0x10: _npc(use_traits=False),
        0x20: _npc(use_traits=False),
    }
    win_lvln = {lchar: _lvln(0x10, 0x20)}
    ps = [_Plugin([_achr(leaf)] * 5)]   # 5 placed guards
    plans = plan_injections(ps, win_npc, win_lvln)
    assert set(plans) == {lvlsec}
    p = plans[lvlsec]
    assert p.instances == 5
    assert p.faces == {0x10, 0x20}
    assert p.variant_base == 0x10       # min of the owners below
    assert p.k == variant_count(5)


def test_plan_injects_at_leaf_when_immediate_target_is_leveled():
    # leaf -> LVLN directly: no NPC template, so the leaf itself is the node.
    leaf, lchar = 0x100, 0x300
    win_npc = {leaf: _npc(use_traits=True, tplt=lchar),
               0x10: _npc(False), 0x20: _npc(False)}
    win_lvln = {lchar: _lvln(0x10, 0x20)}
    ps = [_Plugin([_achr(leaf)] * 4)]
    plans = plan_injections(ps, win_npc, win_lvln)
    assert set(plans) == {leaf}
    assert plans[leaf].faces == {0x10, 0x20}


def test_plan_skips_chain_with_enough_faces():
    # A leaf reaching >= SUFFICIENT_FACES distinct owners is already varied.
    leaf, lchar = 0x100, 0x300
    owners = list(range(0x10, 0x10 + SUFFICIENT_FACES))   # exactly the floor
    win_npc = {leaf: _npc(use_traits=True, tplt=lchar)}
    win_npc.update({o: _npc(False) for o in owners})
    win_lvln = {lchar: _lvln(*owners)}
    ps = [_Plugin([_achr(leaf)] * 50)]
    assert plan_injections(ps, win_npc, win_lvln) == {}


def test_plan_skips_below_instance_threshold():
    leaf, lvlsec, lchar = 0x100, 0x200, 0x300
    win_npc = {leaf: _npc(use_traits=True, tplt=lvlsec),
               lvlsec: _npc(use_traits=True, tplt=lchar),
               0x10: _npc(False)}
    win_lvln = {lchar: _lvln(0x10)}
    ps = [_Plugin([_achr(leaf)] * (EXPAND_THRESHOLD - 1))]
    assert plan_injections(ps, win_npc, win_lvln) == {}


def test_plan_ignores_direct_non_templated_placements():
    # A directly-placed concrete NPC (not a Use-Traits leaf) is furrified in
    # place, never injected — even when placed many times.
    win_npc = {0x10: _npc(use_traits=False)}
    ps = [_Plugin([_achr(0x10)] * 20)]
    assert plan_injections(ps, win_npc, {}) == {}


def test_plan_skips_leveled_selectable_node():
    # A face owner reached directly (injection node = itself) but ALSO an entry
    # in a leveled list must NOT be injected — redirecting it breaks the leaves
    # that select it from that list (raider Gen-2-synth bug). It falls back to
    # the owner pass instead.
    leaf, owner = 0x100, 0x200
    win_npc = {
        leaf: _npc(use_traits=True, tplt=owner),      # leaf -> owner directly
        owner: _npc(use_traits=False),
    }
    # owner is also an entry in some unrelated leveled list
    win_lvln = {0x300: _lvln(owner, 0x201)}
    ps = [_Plugin([_achr(leaf)] * 10)]
    assert plan_injections(ps, win_npc, win_lvln) == {}


def test_plan_aggregates_shared_node_across_leaf_types():
    # Different leaf bases sharing one immediate template accumulate together.
    a, b, lvlsec, lchar = 0x100, 0x101, 0x200, 0x300
    win_npc = {
        a: _npc(use_traits=True, tplt=lvlsec),
        b: _npc(use_traits=True, tplt=lvlsec),
        lvlsec: _npc(use_traits=True, tplt=lchar),
        0x10: _npc(False), 0x20: _npc(False),
    }
    win_lvln = {lchar: _lvln(0x10, 0x20)}
    ps = [_Plugin([_achr(a), _achr(a), _achr(b), _achr(b)])]
    plans = plan_injections(ps, win_npc, win_lvln)
    assert set(plans) == {lvlsec}
    assert plans[lvlsec].instances == 4
