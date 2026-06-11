"""Tests for the headpart picker (HeadpartPools.pick).

Uses a fake pool so the test is pure (no game files). Guards: determinism,
sex/type filtering, distinct picks per signature (family members differ), and
the Sex.MALE-is-zero falsy trap that once silently left every male bald.
"""

import pytest

from furrifier_fo4.headparts import HeadpartPools
from furrifier_fo4.models import Sex
from furrifier_fo4.util import hash_string


class FakePools:
    """Minimal stand-in matching HeadpartPools.pool/pick surface."""

    def __init__(self, mapping):
        # mapping: (race, sex, type) -> [edid, ...]
        self._m = mapping

    def pool(self, race, sex, type_name):
        return self._m.get((race, sex, type_name), [])

    def pick(self, race, sex, type_name, signature, seed):
        # Mirror HeadpartPools.pick exactly (sorted + hash_string).
        cands = sorted(self.pool(race, sex, type_name))
        if not cands:
            return None
        return cands[hash_string(signature, seed, len(cands))]


@pytest.fixture
def pools():
    return FakePools({
        ('Fox', Sex.MALE, 'Hair'): ['Hair_A', 'Hair_B', 'Hair_C', 'Hair_D'],
        ('Fox', Sex.FEMALE, 'Hair'): ['Hair_F1', 'Hair_F2'],
        ('Fox', Sex.MALE, 'Eyes'): ['Eyes_1', 'Eyes_2', 'Eyes_3'],
    })


def test_deterministic(pools):
    a = pools.pick('Fox', Sex.MALE, 'Hair', 'NpcA', 4751)
    b = pools.pick('Fox', Sex.MALE, 'Hair', 'NpcA', 4751)
    assert a == b is not None


def test_male_sex_value_zero_works(pools):
    # Regression: Sex.MALE == 0; a truthiness guard would skip males.
    assert Sex.MALE == 0
    assert pools.pick('Fox', Sex.MALE, 'Hair', 'NpcA', 4751) is not None


def test_sex_filters_pool(pools):
    male = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751)
            for i in range(50)}
    female = {pools.pick('Fox', Sex.FEMALE, 'Hair', f'N{i}', 4751)
              for i in range(50)}
    assert male <= {'Hair_A', 'Hair_B', 'Hair_C', 'Hair_D'}
    assert female <= {'Hair_F1', 'Hair_F2'}
    assert not (male & female)  # disjoint pools


def test_empty_pool_returns_none(pools):
    assert pools.pick('Fox', Sex.MALE, 'Scar', 'NpcA', 1) is None


def test_distinct_signatures_spread(pools):
    # Family members keep distinct signatures -> should land on >1 hair across
    # the family (relatives, not clones). Over a handful of sigs we expect
    # more than one distinct result from a 4-entry pool.
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', sig, 4751)
             for sig in ('Finch1', 'Finch2', 'Finch3', 'Finch4')}
    assert len(picks) >= 2


# ---------------------------------------------------------------------------
# Scheme-level headpart exclusion (exact-EDID, applied at pick time).
# These drive the REAL HeadpartPools.pick — _build is bypassed with an empty
# plugin_set and pools are injected, so the actual exclude/whitelist logic runs.
# ---------------------------------------------------------------------------


class _FakeHP:
    """Stand-in HDPT record exposing just the editor_id pick() reads."""

    def __init__(self, edid):
        self.editor_id = edid


def _real_pools(mapping, exclude=()):
    hp = HeadpartPools([], exclude=exclude)  # empty plugin_set -> _build no-op
    for (race, sex, type_name), edids in mapping.items():
        hp._pools[(race, sex)][type_name] = [_FakeHP(e) for e in edids]
    return hp


def test_exclude_filters_pool():
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['Hair_A', 'Hair_B', 'Hair_C', 'Hair_D']},
        exclude={'hair_b', 'hair_d'})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751).editor_id
             for i in range(50)}
    assert picks <= {'Hair_A', 'Hair_C'}


def test_exclude_is_case_insensitive():
    # Scheme lists 'radhair01' (any case); pool EDID 'RadHair01' is excluded.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['RadHair01', 'GoodHair']},
        exclude={'radhair01'})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751).editor_id
             for i in range(20)}
    assert picks == {'GoodHair'}


def test_whitelist_overrides_exclude():
    # An explicit whitelist may name an excluded part — author intent wins.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['Hair_A', 'Hair_B']},
        exclude={'hair_b'})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                        whitelist=['Hair_B']).editor_id
             for i in range(20)}
    assert picks == {'Hair_B'}


def test_exclude_emptying_pool_returns_none():
    pools = _real_pools({('Fox', Sex.MALE, 'Hair'): ['Hair_A']},
                        exclude={'hair_a'})
    assert pools.pick('Fox', Sex.MALE, 'Hair', 'N', 4751) is None


def test_exclude_supports_leading_trailing_wildcards():
    # 'RadHair*' (prefix) and '*Magazine' (suffix) both filter; a normal hair
    # survives. Mixed case proves case-insensitivity through wildcard_match.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): [
            'RadHair01', 'RadHair02', 'HairMagazine', 'NormalHair']},
        exclude={'radhair*', '*magazine'})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751).editor_id
             for i in range(40)}
    assert picks == {'NormalHair'}


def test_exclude_contains_wildcard():
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['HairRadActive', 'PlainHair']},
        exclude={'*Rad*'})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751).editor_id
             for i in range(20)}
    assert picks == {'PlainHair'}


def test_no_exclude_keeps_full_pool():
    # The parts excluded above are reachable when nothing is excluded —
    # proving exclude is what removes them, not the hash.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['Hair_A', 'Hair_B', 'Hair_C', 'Hair_D']})
    picks = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751).editor_id
             for i in range(50)}
    assert {'Hair_B', 'Hair_D'} <= picks


# ---------------------------------------------------------------------------
# Identity-preserving hair mapping: keep the NPC's own hair (or a
# `<prefix><vanilla>` furry variant of it) instead of a blind reroll.
# ---------------------------------------------------------------------------


def test_preserve_vanilla_when_valid():
    # The NPC's own hair is valid for the furry race -> keep it (tier 1), even
    # though the hash would otherwise spread across the pool.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['HairMale01', 'FFO_HairMale01_Fox', 'Z']})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairMale01', variant_prefix='FFO_').editor_id
           for i in range(20)}
    assert got == {'HairMale01'}


def test_preserve_uses_prefix_variant_when_vanilla_absent():
    # Vanilla hair not valid for the race; its FFO_ variant is -> use the variant.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['FFO_HairMale01_Fox', 'FFO_HairMale99_Fox']})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairMale01', variant_prefix='FFO_').editor_id
           for i in range(20)}
    assert got == {'FFO_HairMale01_Fox'}


def test_preserve_variant_beats_exclude():
    # An NPC whose vanilla hair maps to an *excluded* furry variant still gets
    # it (rad-damaged NPC keeps furry rad hair); random NPCs never would.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['FFO_HairRad01_Fox', 'FFO_Normal_Fox']},
        exclude={'*Rad*'})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairRad01', variant_prefix='FFO_').editor_id
           for i in range(20)}
    assert got == {'FFO_HairRad01_Fox'}


def test_preserve_boundary_no_numeric_bleed():
    # vanilla 'HairMale1' must NOT match 'FFO_HairMale10_Lyk' — only the exact
    # or '_'-delimited variant qualifies.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['FFO_HairMale10_Lyk', 'FFO_HairMale1_Fox']})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairMale1', variant_prefix='FFO_').editor_id
           for i in range(20)}
    assert got == {'FFO_HairMale1_Fox'}


def test_preserve_no_match_falls_back_to_exclude_filtered_random():
    # No vanilla and no FFO_ variant -> ordinary exclude-filtered random pick.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['FFO_A_Fox', 'FFO_B_Fox', 'FFO_C_Fox']},
        exclude={'FFO_B_Fox'})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairMale01', variant_prefix='FFO_').editor_id
           for i in range(50)}
    assert got <= {'FFO_A_Fox', 'FFO_C_Fox'}  # B excluded, no preserve match


def test_preserve_without_prefix_only_tier1():
    # No variant_prefix: an exactly-valid vanilla hair is still preserved, but
    # there's no FFO_ fallback tier.
    pools = _real_pools(
        {('Fox', Sex.MALE, 'Hair'): ['HairMale01', 'FFO_HairMale01_Fox']})
    got = {pools.pick('Fox', Sex.MALE, 'Hair', f'N{i}', 4751,
                      preserve_edid='HairMale01').editor_id
           for i in range(20)}
    assert got == {'HairMale01'}
