"""The scalar-or-range mechanism every catalog number goes through.

A catalog may write any slider as a bare number or as a `[lo, hi]` range;
`parse_range` normalizes both to `(lo, hi)` and `pick_range` draws this
NPC's value from it. Expected values are the hash's exact output.
"""

import pytest

from furrifier_fo4.customization import ColorRule
from furrifier_fo4.util import parse_probability, parse_range, pick_range


class TestParseRange:
    def test_bare_number_is_a_degenerate_range(self):
        assert parse_range(0.5) == (0.5, 0.5)
        assert parse_range(2) == (2.0, 2.0)
        assert parse_range(-0.8) == (-0.8, -0.8)

    def test_pair_is_a_range(self):
        assert parse_range([0.2, 0.8]) == (0.2, 0.8)
        assert parse_range((-1, 1)) == (-1.0, 1.0)

    def test_reversed_pair_is_swapped(self):
        assert parse_range([0.8, 0.2]) == (0.2, 0.8)

    @pytest.mark.parametrize("bad", ["big", [1, 2, 3], [1], [], None,
                                     True, ["a", "b"], {"lo": 1}])
    def test_rejected_shapes_return_the_default(self, bad):
        assert parse_range(bad) is None
        assert parse_range(bad, default=(0.0, 1.0)) == (0.0, 1.0)

    def test_rejection_warns_only_when_given_a_context(self, caplog):
        parse_range("big")
        assert caplog.text == ""
        parse_range("big", "Fox/Nose.scale")
        assert "Fox/Nose.scale" in caplog.text


class TestPickRange:
    def test_degenerate_range_ignores_the_signature(self):
        assert pick_range((0.3, 0.3), "AnyNPC", "k") == 0.3
        assert pick_range((0.3, 0.3), "OtherNPC", "k") == 0.3

    def test_pick_is_deterministic(self):
        assert (pick_range((0.0, 1.0), "Preston", "k")
                == pick_range((0.0, 1.0), "Preston", "k"))

    def test_pick_lands_inside_the_range(self):
        for sig in ("Preston", "Codsworth", "Piper", "Nick", "Cait"):
            assert 0.2 <= pick_range((0.2, 0.6), sig, "scale") <= 0.6

    def test_different_npcs_differ(self):
        picks = {pick_range((0.0, 1.0), sig, "scale")
                 for sig in ("Preston", "Codsworth", "Piper", "Nick", "Cait")}
        assert len(picks) > 1

    def test_different_keys_decorrelate_one_npc(self):
        """Two ranged sliders on the same NPC must not move in lockstep."""
        assert (pick_range((0.0, 1.0), "Preston", "Eyes.scale")
                != pick_range((0.0, 1.0), "Preston", "Nose.scale"))


class TestColorRuleNormalizes:
    def test_bare_intensity_becomes_a_range(self):
        rule = ColorRule(colors=[("ffofurwhite", 0.8)])
        assert rule.colors == [("ffofurwhite", (0.8, 0.8))]

    def test_ranged_intensity_passes_through(self):
        rule = ColorRule(colors=[("ffofurwhite", [0.6, 0.9])])
        assert rule.colors == [("ffofurwhite", (0.6, 0.9))]


class TestParseProbability:
    """A probability is a plain number. A range there would behave exactly
    like its midpoint, so it's rejected loudly instead of silently accepted
    (or crashing `float()` on a list, which is what used to happen)."""

    def test_plain_number(self):
        assert parse_probability(0.7) == 0.7
        assert parse_probability(1) == 1.0

    def test_range_is_rejected_with_the_midpoint_named(self, caplog):
        assert parse_probability([0.2, 0.4], "breed ElkBreed") is None
        assert "breed ElkBreed" in caplog.text
        assert "0.3" in caplog.text          # names the number to use instead

    def test_range_falls_back_to_the_default(self):
        assert parse_probability([0.2, 0.4], "ctx", 1.0) == 1.0

    @pytest.mark.parametrize("bad", ["high", None, True, [1, 2, 3], {}])
    def test_other_junk_returns_the_default(self, bad):
        assert parse_probability(bad, "ctx") is None
