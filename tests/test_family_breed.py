"""Real-data spot-check: a family shares not just a RACE but a BREED.

The signature-level mechanism is unit-tested hermetically in
`test_scheme.py::test_family_shares_breed_signature` (it proves
`breed_signature_for` collapses every family member to the leader). This test
closes the loop on the REAL shipping catalog: under the default `ffo_scheme`,
the actual ROLLED breed — not just the breed signature — comes out shared across
a family, exercising `FurryWorld`'s full resolution path, i.e. exactly what
`session.run` / the preview do per NPC.

`@pytest.mark.gamefiles`: needs FO4 + FurryFallout.esp installed (for the NPC
records, the RELA-derived families, and the deer breed catalog).

The anchor pair is **Kyle & Riley** (`FFDiamondCity12Kyle`/`Riley`). Of the
families with a furry assignment they're the DISCRIMINATING one: FFODeerRace has
seven breeds, and Kyle's OWN signature rolls a *different* breed
(`WhiteTailBreed`) than the family-shared roll (`ReindeerBreed`) — so this pair
actually regresses to a visible mismatch if the breed-sharing breaks. The fox
families (John/Cathy, the Bobrov brothers) both roll the breedless fox default
(`None`), so they only exercise race-sharing; they're kept as lighter coverage
but would NOT catch a breed-sharing regression on their own.
"""

from __future__ import annotations

import pytest


def _resolve(world, edid):
    """(parent_race, breed_name) for an NPC EditorID, mirroring the breed
    resolution in `preview/session.bake` / `session.do_furrify`. Returns
    (None, None) when the NPC is absent or gated/left-human."""
    npc = world._npc_by_edid.get(edid)
    if npc is None:
        return (None, None)
    race_name = world.resolved_race(npc)
    if race_name is None:
        return (None, None)
    parent_race, breed = world.cust.resolve_race_or_breed(race_name)
    if breed is None:
        sig = world.scheme.signature_for(edid)
        breed_sig = world.scheme.breed_signature_for(edid)
        breed = world.cust.roll_breed(breed_sig or sig, parent_race)
    return (parent_race, breed.name if breed else None)


@pytest.fixture(scope="module")
def world():
    """The default-scheme world, loaded once. Skips when FO4 isn't installed."""
    from esplib import find_game_data
    try:
        find_game_data("fo4")
    except Exception as exc:  # noqa: BLE001 - any locate failure -> skip
        pytest.skip(f"FO4 game files not available: {exc}")
    from furrifier_fo4.world import FurryWorld
    w = FurryWorld("ffo_scheme", progress=lambda _m: None)
    try:
        yield w
    finally:
        w.close()


# (leader, member) pairs Hugh named. Leader first (matches builtin.toml order).
_PAIRS = [
    ("FFDiamondCity12Riley", "FFDiamondCity12Kyle"),
    ("Cathy", "John"),
    ("VadimBobrov", "YefimBobrov"),
]


@pytest.mark.gamefiles
@pytest.mark.parametrize("leader,member", _PAIRS)
def test_family_shares_race_and_breed(world, leader, member):
    """Every member of a family resolves to the SAME (race, breed) as its
    leader — the core invariant. (For the fox pairs both are the breedless
    default; the assertion still guards that they never diverge.)"""
    assert _resolve(world, leader) == _resolve(world, member), (
        f"{member} must share {leader}'s race AND breed")


@pytest.mark.gamefiles
def test_deer_family_shares_a_real_breed(world):
    """Kyle & Riley share a concrete (non-default) breed. Pins the current
    shipping value so a silent catalog/seed change is visible; the load-bearing
    part is that the breed is not None and identical for both."""
    riley = _resolve(world, "FFDiamondCity12Riley")
    kyle = _resolve(world, "FFDiamondCity12Kyle")
    assert riley == ("FFODeerRace", "ReindeerBreed")
    assert kyle == riley
    assert riley[1] is not None  # a real breed, not the vacuous breedless case


@pytest.mark.gamefiles
def test_breed_sharing_is_load_bearing(world):
    """Without the family-shared signature, Kyle would roll a DIFFERENT deer
    breed than Riley — proving the sharing is doing real work here (and that
    `test_deer_family_shares_a_real_breed` isn't passing by coincidence)."""
    parent, _ = world.cust.resolve_race_or_breed(
        world.resolved_race(world._npc_by_edid["FFDiamondCity12Kyle"]))
    own = world.cust.roll_breed(
        world.scheme.signature_for("FFDiamondCity12Kyle"), parent)
    shared = world.cust.roll_breed(
        world.scheme.breed_signature_for("FFDiamondCity12Kyle"), parent)
    assert own is not None and shared is not None
    assert own.name != shared.name, (
        "Kyle's own-signature breed must differ from the family-shared breed, "
        "else this pair can't detect a sharing regression")
    # Current shipping values (deterministic, seeded roll).
    assert own.name == "WhiteTailBreed"
    assert shared.name == "ReindeerBreed"
