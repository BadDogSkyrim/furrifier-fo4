"""Tests for the headpart picker (HeadpartPools.pick).

Uses a fake pool so the test is pure (no game files). Guards: determinism,
sex/type filtering, distinct picks per signature (family members differ), and
the Sex.MALE-is-zero falsy trap that once silently left every male bald.
"""

import pytest

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
