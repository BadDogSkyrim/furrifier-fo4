"""Per-race appearance customization from the race catalog (races/*.toml).

Parses [[race_customization]] rows into lookups the furrifier consults when
building an NPC:

  - child_race:   adult race EDID -> child race EDID
  - weight_range: (race, sex) -> 3 (lo,hi) ranges remapping MWGT thin/musc/fat
  - headpart rule: (race, sex, HP_TYPE) -> (probability, whitelist) gating and
    constraining headpart-type assignment (e.g. EYEBROWS = 0.2, or
    FACIAL_HAIR = {probability=1.0, headpart=["FFOBeard01"]})
  - colors:       (race) -> named color-scheme reference (applied to tints)

Sex resolution: a row's `sex` is 'male'/'female'/'both' (or omitted = both,
stored as None). Lookups try the specific sex, then the both/None entry.
Mirrors the Skyrim furrifier's RaceDefContext, FO4-shaped (3-axis weight).
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Optional

from .models import Sex

log = logging.getLogger(__name__)

# race_customization key (uppercase) -> headpart category name used by
# HeadpartPools. Only the gated/whitelisted types are configurable here;
# Face/Eyes/Hair are always attempted.
_HP_KEYS = {
    'EYEBROWS': 'Eyebrows',
    'FACIAL_HAIR': 'Facial Hair',
    'SCAR': 'Scar',
    'HAIR': 'Hair',
    'EYES': 'Eyes',
    'FACE': 'Face',
}


class HeadpartRule:
    __slots__ = ('probability', 'whitelist')

    def __init__(self, probability: float = 1.0, whitelist=()):
        self.probability = probability
        self.whitelist = tuple(whitelist)


class ColorRule:
    """A color scheme's constraint for one tint category.

    `probability` gates whether the category applies. `colors` is an ordered
    list of (clfm_edid_lower, intensity) — the allowed palette; empty means
    "any color in the race's TTEC for this category" (probability only).
    """

    __slots__ = ('probability', 'colors')

    def __init__(self, probability: float = 1.0, colors=()):
        self.probability = probability
        self.colors = list(colors)


class Customization:
    """All race-appearance customization, keyed for lookup."""

    def __init__(self):
        self.child_races: dict[str, str] = {}
        # (race, sex|None) -> [(lo,hi), (lo,hi), (lo,hi)]  thin/musc/fat
        self.weight_ranges: dict[tuple, list] = {}
        # (race, sex|None, HP_KEY) -> HeadpartRule
        self.headpart_rules: dict[tuple, HeadpartRule] = {}
        # race -> color scheme name
        self.colors: dict[str, str] = {}
        # scheme name -> category name -> ColorRule
        self.color_schemes: dict[str, dict] = {}


    def child_race(self, race_edid: str) -> Optional[str]:
        return self.child_races.get(race_edid)


    def color_scheme_for(self, race_edid: str) -> Optional[dict]:
        """category -> ColorRule for a race's named scheme, or None (no
        scheme = use the race's full palette)."""
        name = self.colors.get(race_edid)
        return self.color_schemes.get(name) if name else None


    def weight_range(self, race_edid: str, sex: Sex):
        """Return [(lo,hi)*3] for thin/musc/fat, or None (no remap)."""
        for key in ((race_edid, _sex_token(sex)), (race_edid, None)):
            r = self.weight_ranges.get(key)
            if r is not None:
                return r
        return None


    def headpart_rule(self, race_edid: str, sex: Sex,
                      hp_key: str) -> HeadpartRule:
        """Rule for a headpart type; default (prob 1.0, no whitelist)."""
        for key in ((race_edid, _sex_token(sex), hp_key),
                    (race_edid, None, hp_key)):
            r = self.headpart_rules.get(key)
            if r is not None:
                return r
        return HeadpartRule()


def _sex_token(sex: Sex) -> str:
    return 'female' if sex == Sex.FEMALE else 'male'


def _normalize_sex(raw) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ('both', ''):
        return None
    if s in ('male', 'female'):
        return s
    log.warning("race_customization: unknown sex %r (treating as both)", raw)
    return None


def _parse_headpart_value(val) -> HeadpartRule:
    """A headpart key value is either a bare probability (0.2) or an inline
    table {probability=, headpart=[...]}."""
    if isinstance(val, (int, float)):
        return HeadpartRule(probability=float(val))
    if isinstance(val, dict):
        prob = float(val.get('probability', 1.0))
        wl = val.get('headpart', ())
        if isinstance(wl, str):
            wl = [wl]
        return HeadpartRule(probability=prob, whitelist=wl)
    log.warning("race_customization: bad headpart value %r", val)
    return HeadpartRule()


def load_customization(races_dir: Path) -> Customization:
    """Merge every races/*.toml [[race_customization]] row into one store."""
    cust = Customization()
    if not races_dir.is_dir():
        return cust
    for toml_path in sorted(races_dir.glob('*.toml')):
        with open(toml_path, 'rb') as f:
            data = tomllib.load(f)
        for row in data.get('race_customization', []):
            _apply_row(cust, row, toml_path.name)
        for name, block in data.get('color_schemes', {}).items():
            cust.color_schemes[name] = _parse_color_scheme(block, name,
                                                           toml_path.name)
    return cust


def _parse_color_scheme(block: dict, name: str, source: str) -> dict:
    """Parse one [color_schemes.NAME] block: category -> ColorRule.

    Category keys are underscored in TOML (Muzzle_Stripe) -> de-underscored
    to match tint categories ('Muzzle Stripe'). Each value is a list whose
    optional leading ['probability', p] sets the gate; remaining
    [colorEdid, intensity] pairs are the allowed palette.
    """
    out: dict = {}
    for raw_cat, entries in block.items():
        category = raw_cat.replace('_', ' ')
        prob = 1.0
        colors = []
        for entry in entries:
            if (isinstance(entry, list) and len(entry) == 2
                    and isinstance(entry[0], str)):
                if entry[0].lower() == 'probability':
                    prob = float(entry[1])
                else:
                    colors.append((entry[0].lower(), float(entry[1])))
            else:
                log.warning("%s color_scheme %s/%s: bad entry %r",
                            source, name, raw_cat, entry)
        out[category] = ColorRule(probability=prob, colors=colors)
    return out


def _apply_row(cust: Customization, row: dict, source: str) -> None:
    race = row.get('race')
    if not race:
        log.warning("%s: race_customization row missing 'race'", source)
        return
    sex = _normalize_sex(row.get('sex'))

    if row.get('child_race'):
        cust.child_races[race] = row['child_race']

    wr = row.get('weight_range')
    if wr is not None:
        ranges = _parse_weight_range(wr, race, source)
        if ranges is not None:
            cust.weight_ranges[(race, sex)] = ranges

    if row.get('colors'):
        cust.colors[race] = row['colors']

    for key, hp_cat in _HP_KEYS.items():
        if key in row:
            cust.headpart_rules[(race, sex, key)] = \
                _parse_headpart_value(row[key])


def _parse_weight_range(wr, race: str, source: str):
    """Accept [[lo,hi],[lo,hi],[lo,hi]] (thin/musc/fat). Returns list of
    3 (lo,hi) tuples in 0-1 space, or None on malformed input."""
    if (not isinstance(wr, list) or len(wr) != 3
            or any(not isinstance(p, list) or len(p) != 2 for p in wr)):
        log.warning("%s: %s weight_range must be 3 [lo,hi] pairs, got %r",
                    source, race, wr)
        return None
    out = []
    for lo, hi in wr:
        # Scheme uses 0-100; MWGT axes are 0-1 floats.
        out.append((float(lo) / 100.0, float(hi) / 100.0))
    return out
