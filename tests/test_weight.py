"""Tests for the MWGT (body weight) summing-to-1 algorithm (furrify._compute_weights)."""

import pytest

from furrifier_fo4.furrify import _compute_weights


def _sum1(vals):
    return abs(sum(vals) - 1.0) < 1e-6


def test_slack_axis_absorbs_residual():
    # thin + fat pinned (fixed), muscle (1) omitted -> residual.
    v = _compute_weights({0: (0.4, 0.4), 2: (0.2, 0.2)}, [0, 0, 0], 'npc')
    assert v == pytest.approx([0.4, 0.4, 0.2])   # muscle = 1 - 0.4 - 0.2
    assert _sum1(v)


def test_two_omitted_split_residual():
    v = _compute_weights({0: (0.6, 0.6)}, [0, 0, 0], 'npc')
    assert v == pytest.approx([0.6, 0.2, 0.2])   # muscle + fat each get half of 0.4
    assert _sum1(v)


def test_all_three_pinned_normalized():
    v = _compute_weights({0: (0.5, 0.5), 1: (0.5, 0.5), 2: (0.5, 0.5)},
                         [0, 0, 0], 'npc')
    assert all(abs(x - 1 / 3) < 1e-6 for x in v)   # 0.5/1.5 each
    assert _sum1(v)


def test_pinned_exceed_one_best_effort_normalized():
    # thin + fat pinned to 0.8 each (sum 1.6 > 1); slack can't go negative.
    v = _compute_weights({0: (0.8, 0.8), 2: (0.8, 0.8)}, [0, 0, 0], 'npc')
    assert v == pytest.approx([0.5, 0.0, 0.5])
    assert _sum1(v)


def test_no_spec_normalizes_raw_mwgt():
    v = _compute_weights(None, [0.4, 0.4, 0.4], 'npc')   # sum 1.2 -> normalize
    assert all(abs(x - 1 / 3) < 1e-6 for x in v)
    assert _sum1(v)


def test_no_spec_all_zero_stays_base_body():
    assert _compute_weights(None, [0.0, 0.0, 0.0], 'npc') == [0.0, 0.0, 0.0]


def test_pinned_ceiling_always_respected_across_signatures():
    # Cheetah: fat capped at 0.2, thin 0.4-1.0, muscle the slack.
    spec = {0: (0.4, 1.0), 2: (0.0, 0.2)}
    for i in range(200):
        v = _compute_weights(spec, [0, 0, 0], f'cheetah_{i}')
        assert v[2] <= 0.2 + 1e-9         # fat never exceeds its ceiling
        assert _sum1(v)
        assert all(x >= -1e-9 for x in v)
