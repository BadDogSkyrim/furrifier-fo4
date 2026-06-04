"""Typed data models for the FO4 furrifier scheme/catalog format.

Mirrors PLAN_FO4_SCHEME.md. The central difference from Skyrim is the
class-based distribution model: NPCs are sorted into classes by their
characteristics, and each class distributes furry races by weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from esplib import Record


# Fields a class-match rule can test against. Stored lowercased.
MATCH_FIELDS = ('race', 'faction', 'editorid', 'name')

# The only vanilla races eligible for furrification (the candidate gate,
# PLAN_FO4_SCHEME.md line 13). Everything else (synths, robots, super
# mutants, creatures, turrets) is never furrified.
FURRIFIABLE_RACES = frozenset({
    'HumanRace', 'HumanChildRace', 'GhoulRace', 'GhoulChildRace',
})

# Races that, when named as a distribution target, mean "leave human"
# (PLAN_FO4_SCHEME.md line 133). A weight on these reserves a slice of a
# class for NPCs that should not be furrified.
NON_FURRY_TARGETS = frozenset({'HumanRace', 'GhoulRace'})

# Stamped into the patch's TES4 author on save. Lets a later run (or the
# preview) recognize an NPC the furrifier already furrified — by checking the
# author of the plugin its winning override came from — regardless of the
# patch's filename.
FURRIFIER_AUTHOR = "FO4Furrifier"


def is_furrifier_plugin(plugin) -> bool:
    """True if `plugin` is a furrifier output (its TES4 author is stamped
    FURRIFIER_AUTHOR). `plugin` may be None."""
    return bool(plugin is not None
                and getattr(plugin.header, "author", None) == FURRIFIER_AUTHOR)


class Sex(IntEnum):
    MALE = 0
    FEMALE = 1

    @property
    def is_female(self) -> bool:
        return self is Sex.FEMALE


@dataclass
class MatchRule:
    """One class-membership test: (class_name, field, pattern).

    `field` is one of MATCH_FIELDS. `pattern` allows '*' at start and/or
    end (see util.wildcard_match). Rules are evaluated in order; the first
    matching rule's class wins.
    """
    class_name: str
    field: str
    pattern: str


@dataclass
class WeightedRace:
    """One furry-race target with an integer weight inside a class."""
    race: str
    weight: int


@dataclass
class ClassDistribution:
    """The weighted furry-race targets for a single class."""
    class_name: str
    races: list[WeightedRace] = field(default_factory=list)

    @property
    def total_weight(self) -> int:
        return sum(r.weight for r in self.races)


@dataclass
class Breed:
    """A constrained visual flavor of a parent furry race (engine race
    unchanged; only narrows headpart/tint picks). Same semantics as the
    Skyrim furrifier."""
    name: str
    parent_race_edid: str
    probability: float = 0.0


@dataclass
class RaceCustomization:
    """Per (race-or-breed, sex) appearance customization."""
    race: str
    sex: Optional[Sex] = None          # None = both
    child_race: Optional[str] = None   # explicit child-race EDID, if any
    weight_range: Optional[list] = None  # [[thin], [musc], [fat]] ranges
    colors: Optional[str] = None       # named color scheme
    # headpart picks keyed by HeadpartType name -> rule dict
    headparts: dict = field(default_factory=dict)


@dataclass
class RaceInfo:
    """Pre-indexed data about a furry (or vanilla) race record."""
    record: Record
    editor_id: str
    is_child: bool = False
    child_race: Optional[str] = None
    # headparts[Sex][hp_type_name] -> list[Record]
    headparts: dict = field(default_factory=dict)
    # tint groups keyed by TTGP name -> list of options
    tint_groups: dict = field(default_factory=dict)
