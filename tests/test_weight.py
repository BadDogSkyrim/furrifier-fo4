"""Tests for the MWGT (body weight) remap (furrify._compute_weights / _weight_is_garbage).

Pinned axes map linearly from the NPC's original value into their band; the rest
fill to sum 1.0 preserving build. Garbage MWGT (out of [0,1]) is passed through.
"""

import pytest

from furrifier_fo4.furrify import _compute_weights, _weight_source

FLT_MAX = 3.4028234663852886e38


def _sum1(v):
    return abs(sum(v) - 1.0) < 1e-6


def test_pinned_axis_maps_linearly():
    # thin band [0.4, 1.0]: orig 0 -> 0.4, orig 1 -> 1.0, orig 0.5 -> 0.7.
    assert _compute_weights({0: (0.4, 1.0)}, [0.0, 0.5, 0.5])[0] == pytest.approx(0.4)
    assert _compute_weights({0: (0.4, 1.0)}, [1.0, 0.5, 0.5])[0] == pytest.approx(1.0)
    assert _compute_weights({0: (0.4, 1.0)}, [0.5, 0.5, 0.5])[0] == pytest.approx(0.7)


def test_one_pinned_others_keep_original_ratio():
    # thin pinned -> 0.5; muscle:fat original 0.6:0.2 = 3:1; residual 0.5 split 3:1.
    v = _compute_weights({0: (0.5, 0.5)}, [0.0, 0.6, 0.2])
    assert v == pytest.approx([0.5, 0.375, 0.125])
    assert _sum1(v)


def test_one_pinned_others_both_zero_even_split():
    v = _compute_weights({0: (0.6, 0.6)}, [0.0, 0.0, 0.0])
    assert v == pytest.approx([0.6, 0.2, 0.2])
    assert _sum1(v)


def test_two_pinned_remainder_fills_to_one():
    # thin -> 0.4, fat -> 0.2 (fixed bands); muscle (omitted) = 0.4.
    v = _compute_weights({0: (0.4, 0.4), 2: (0.2, 0.2)}, [0.5, 0.5, 0.5])
    assert v == pytest.approx([0.4, 0.4, 0.2])
    assert _sum1(v)


def test_two_pinned_over_one_best_effort_normalized():
    v = _compute_weights({0: (0.8, 0.8), 2: (0.8, 0.8)}, [0.5, 0.5, 0.5])
    assert v == pytest.approx([0.5, 0.0, 0.5])
    assert _sum1(v)


def test_three_pinned_mapped_then_normalized():
    # all map to 0.5 -> normalize -> 1/3 each.
    v = _compute_weights({0: (0.5, 0.5), 1: (0.5, 0.5), 2: (0.5, 0.5)},
                         [0.5, 0.5, 0.5])
    assert all(abs(x - 1 / 3) < 1e-6 for x in v)
    assert _sum1(v)


def test_pinned_ceiling_holds_for_real_builds():
    # Cheetah: fat band [0, 0.2]; even a maxed-fat NPC can't exceed 0.2.
    spec = {0: (0.4, 1.0), 2: (0.0, 0.2)}
    for thin, musc, fat in [(0, 0, 0), (1, 1, 1), (0.3, 0.6, 0.9), (1, 0, 0)]:
        v = _compute_weights(spec, [thin, musc, fat])
        assert v[2] <= 0.2 + 1e-9
        assert _sum1(v)
        assert all(x >= -1e-9 for x in v)


def test_weight_source_valid_passthrough_garbage_random():
    # Valid axes kept as-is; FLT_MAX axes become a pseudo-random fraction in [0,1].
    src = _weight_source([0.3, FLT_MAX, 0.9], 'npc')
    assert src[0] == 0.3 and src[2] == 0.9
    assert 0.0 <= src[1] <= 1.0 and src[1] not in (0.3, 0.9)
    # Deterministic per signature; varies across signatures.
    assert _weight_source([FLT_MAX] * 3, 'npc') == _weight_source([FLT_MAX] * 3, 'npc')
    assert _weight_source([FLT_MAX] * 3, 'a') != _weight_source([FLT_MAX] * 3, 'b')


def test_all_random_maps_into_band_sums_to_one():
    # All-FLT_MAX NPC with a weight_range: each axis a race-appropriate roll.
    spec = {0: (0.4, 1.0), 2: (0.0, 0.2)}
    for i in range(100):
        src = _weight_source([FLT_MAX] * 3, f'rng_{i}')
        v = _compute_weights(spec, src)
        assert 0.4 - 1e-9 <= v[0] <= 1.0 + 1e-9    # thin stays in its band
        assert v[2] <= 0.2 + 1e-9                  # fat stays in its band
        assert _sum1(v)
