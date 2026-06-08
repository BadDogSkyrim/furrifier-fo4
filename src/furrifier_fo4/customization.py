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

from .facemorphs import parse_facemorphs
from .models import Breed, Sex
from .util import hash_string

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

# Structural row keys (everything else is an inline color-rule key, validated by
# shape: a list of [key, value] entries).
_RESERVED_ROW_KEYS = {'race', 'sex', 'child_race', 'weight_range', 'colors'}


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
        # scheme name -> category-or-layer key -> ColorRule
        self.color_schemes: dict[str, dict] = {}
        # race -> {category_key_lower: [layer-name glob patterns]}, from the
        # [tint_categories] block of the FILE that defines the race (file-scoped,
        # so categories don't bleed between race catalogs).
        self.tint_categories: dict[str, dict] = {}
        # Breeds: visual flavors of a parent race (engine race unchanged; they
        # narrow headpart/tint/weight picks). name -> Breed, and parent race ->
        # [Breed] in definition order for the probability roll.
        self.breeds: dict[str, Breed] = {}
        self.breeds_by_parent: dict[str, list] = {}
        # race-or-breed name -> FaceMorphSpec (head-shaping morphs).
        self.facemorphs: dict = {}


    def child_race(self, race_edid: str) -> Optional[str]:
        return self.child_races.get(race_edid)


    def set_breed(self, name: str, parent_race_edid: str,
                  probability: float = 0.0) -> None:
        """Register a breed under its parent race. Probabilities for one parent
        must sum to <= 1.0 (the remainder is the breed-less slice); an entry
        that overflows is dropped with a warning rather than aborting the load."""
        existing = sum(b.probability for b in
                       self.breeds_by_parent.get(parent_race_edid, []))
        if existing + probability > 1.0 + 1e-9:
            log.warning("breeds for %s would exceed probability 1.0 "
                        "(%.3f); dropping breed %r", parent_race_edid,
                        existing + probability, name)
            return
        breed = Breed(name=name, parent_race_edid=parent_race_edid,
                      probability=probability)
        self.breeds[name] = breed
        self.breeds_by_parent.setdefault(parent_race_edid, []).append(breed)

    def resolve_race_or_breed(self, name: str):
        """(parent_race_edid, Breed) if `name` is a registered breed, else
        (name, None). Lets a scheme target a breed name directly."""
        breed = self.breeds.get(name)
        if breed is not None:
            return breed.parent_race_edid, breed
        return name, None

    def roll_breed(self, signature: str, parent_race_edid: str):
        """Deterministically pick a breed for `parent_race_edid`, hashed on
        `signature`, or None if the roll lands in the breed-less slice (always
        for a race with no breeds). Mirrors the Skyrim furrifier (seed 7919,
        10000 buckets); probability-0 breeds are never auto-picked."""
        breeds = self.breeds_by_parent.get(parent_race_edid)
        if not breeds:
            return None
        # Buckets MUST equal hash_string's internal modulus (16000): a smaller
        # modulus (e.g. 10000) reduces non-uniformly (h % 10000 double-counts
        # [0,6000)), which skews the distribution toward the earlier breeds.
        buckets = 16000
        # This Pascal-port hash has weak avalanche, so prefix-shared signatures
        # (variant edids `<owner>_F00`..`_F23`) would all roll the SAME breed —
        # 24 identical-breed clones, defeating variant-expansion. Mixing a
        # forward and reversed hash moves the varying suffix to the front and
        # de-clusters them, while diverse real edids stay well distributed.
        roll = (hash_string(signature, 7919, buckets)
                + hash_string(signature[::-1], 7919, buckets)) % buckets
        cumulative = 0
        for breed in breeds:
            width = int(round(breed.probability * buckets))
            if width == 0:
                continue
            if roll < cumulative + width:
                return breed
            cumulative += width
        return None

    def _lookup_races(self, race_or_breed: str):
        """Race keys to try for a customization lookup, in breed -> parent ->
        '*' order (a bare race yields race -> '*')."""
        breed = self.breeds.get(race_or_breed)
        if breed is not None:
            yield breed.name
            yield breed.parent_race_edid
        else:
            yield race_or_breed
        yield '*'

    def color_scheme_for(self, race_or_breed: str) -> Optional[dict]:
        """key -> ColorRule for a race-or-breed's named scheme, or None (no
        scheme = use the race's full palette). Falls back breed -> parent -> '*';
        keys are category-or-layer names."""
        for race in self._lookup_races(race_or_breed):
            name = self.colors.get(race)
            if name:
                return self.color_schemes.get(name)
        return None

    def facemorphs_for(self, race_or_breed: str):
        """FaceMorphSpec for a race-or-breed, or None. A breed uses its own
        entry if defined, else its parent race's; no '*' wildcard (regions are
        race-specific). [[facemorphs]] head-shaping morphs."""
        breed = self.breeds.get(race_or_breed)
        if breed is not None:
            return (self.facemorphs.get(breed.name)
                    or self.facemorphs.get(breed.parent_race_edid))
        return self.facemorphs.get(race_or_breed)

    def categories_for(self, race_edid: str) -> dict:
        """{category_key_lower: [patterns]} for the race's file, or {} if its
        file defined no [tint_categories]. Drives which tint-layer names a
        category key resolves to (a scheme/default key that isn't a category is
        treated as an exact layer name)."""
        return self.tint_categories.get(race_edid, {})


    def weight_range(self, race_or_breed: str, sex: Sex):
        """Return [(lo,hi)*3] for thin/musc/fat, or None (no remap). Falls back
        breed -> parent -> '*'."""
        for race in self._lookup_races(race_or_breed):
            for sx in (_sex_token(sex), None):
                r = self.weight_ranges.get((race, sx))
                if r is not None:
                    return r
        return None


    def headpart_rule(self, race_or_breed: str, sex: Sex,
                      hp_key: str) -> HeadpartRule:
        """Rule for a headpart type; default (prob 1.0, no whitelist). Falls
        back breed -> parent -> '*', so a breed that's silent on a type inherits
        its parent's rule."""
        for race in self._lookup_races(race_or_breed):
            for sx in (_sex_token(sex), None):
                r = self.headpart_rules.get((race, sx, hp_key))
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


# The only top-level tables/keys a races/*.toml may define. A key outside this
# set is almost always a typo (e.g. `color_scheme`, `breed`, `race_customizaton`)
# whose whole section would otherwise vanish silently — warn loudly instead.
_TOP_LEVEL_KEYS = {'race_customization', 'color_schemes', 'tint_categories',
                   'breeds', 'facemorphs'}


def load_customization(races_dir: Path) -> Customization:
    """Merge every races/*.toml [[race_customization]] row into one store.

    `[tint_categories]` is FILE-SCOPED: each file's categories attach only to
    the races that file defines, so catalogs stay self-contained."""
    cust = Customization()
    if not races_dir.is_dir():
        return cust
    for toml_path in sorted(races_dir.glob('*.toml')):
        with open(toml_path, 'rb') as f:
            data = tomllib.load(f)
        for key in data:
            if key not in _TOP_LEVEL_KEYS:
                log.warning("%s: unrecognized top-level key %r - ignored "
                            "(expected one of %s)", toml_path.name, key,
                            ', '.join(sorted(_TOP_LEVEL_KEYS)))
        file_categories = _parse_tint_categories(data.get('tint_categories', {}))
        for row in data.get('race_customization', []):
            _apply_row(cust, row, toml_path.name)
            race = row.get('race')
            if race and file_categories:
                cust.tint_categories[race] = file_categories
        for name, block in data.get('color_schemes', {}).items():
            cust.color_schemes[name] = _parse_color_scheme(block, name,
                                                           toml_path.name)
        for entry in data.get('breeds', []):
            breed = entry.get('breed')
            parent = entry.get('race')
            if not breed or not parent:
                log.warning("%s: breed entry missing 'breed' or 'race': %r",
                            toml_path.name, entry)
                continue
            cust.set_breed(breed, parent, float(entry.get('probability', 0.0)))
        for name, blocks in data.get('facemorphs', {}).items():
            cust.facemorphs[name] = parse_facemorphs(blocks, name)
    return cust


def _parse_tint_categories(block: dict) -> dict:
    """[tint_categories] -> {category_key_lower: [layer-name glob patterns]}.

    Keys are de-underscored + lowercased so a scheme key (`Muzzle_Stripe`) and
    the category key (`Muzzle Stripe`) resolve the same. A value is a list of
    case-insensitive globs over tint-layer (TTGP) names; a literal (no `*`)
    matches that exact layer. Insertion order is preserved (= no-scheme apply
    order)."""
    out: dict = {}
    for raw_cat, patterns in block.items():
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            log.warning("tint_categories %r: expected a list of patterns, got "
                        "%r", raw_cat, patterns)
            continue
        out[raw_cat.replace('_', ' ').lower()] = [str(p) for p in patterns]
    return out


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

    # Color scheme: either a `colors = "Name"` reference to a shared
    # [color_schemes.Name] block, or inline category rules written directly on
    # the row. An inline key is any non-reserved key whose value is a list of
    # [key, value] entries (the color-rule shape); it synthesizes a private
    # scheme for this race. Inline rules win over a `colors` reference.
    inline = {}
    for key, val in row.items():
        if key in _RESERVED_ROW_KEYS or key in _HP_KEYS:
            continue
        if isinstance(val, list) and val and all(isinstance(e, list) for e in val):
            inline[key] = val
        elif isinstance(val, list):
            # Looks like an attempted inline color rule but isn't a list of
            # [key, value] pairs (e.g. a flat `["probability", 1.0, [...]]`).
            log.warning("%s: %s: malformed color rule %r - expected a list of "
                        "[key, value] pairs like "
                        "[[\"probability\", 1.0], [\"FFOFurWhite\", 0.8]], got %r; "
                        "rule ignored", source, race, key, val)
        else:
            log.warning("%s: %s: unrecognized race_customization key %r - ignored",
                        source, race, key)
    if inline:
        scheme_name = f"__inline__{race}"
        cust.color_schemes[scheme_name] = _parse_color_scheme(
            inline, scheme_name, source)
        if row.get('colors'):
            log.warning("%s: %s has both colors=%r and inline tint rules; "
                        "inline rules win", source, race, row['colors'])
        cust.colors[race] = scheme_name
    elif row.get('colors'):
        cust.colors[race] = row['colors']

    for key, hp_cat in _HP_KEYS.items():
        if key in row:
            cust.headpart_rules[(race, sex, key)] = \
                _parse_headpart_value(row[key])


_WEIGHT_AXES = {'thin': 0, 'muscle': 1, 'muscular': 1, 'musc': 1, 'fat': 2}


def _parse_weight_range(wr, race: str, source: str):
    """Parse a weight_range into `{axis_index: (lo, hi)}` (0-1) for the
    SPECIFIED axes (0=thin, 1=muscular, 2=fat).

    Preferred form is a table keyed by axis name, e.g.
    `weight_range = {thin = [40, 100], fat = [0, 20]}` — pin the axes you care
    about and OMIT one to make it the slack that brings the body to sum 1. The
    legacy `[[lo,hi],[lo,hi],[lo,hi]]` (all three, in order) is still accepted.
    0-100 -> 0-1. Returns None on malformed/empty input."""
    spec = {}
    if isinstance(wr, dict):
        for name, rng in wr.items():
            idx = _WEIGHT_AXES.get(str(name).strip().lower())
            if idx is None:
                log.warning("%s: %s weight_range: unknown axis %r (use "
                            "thin/muscle/fat)", source, race, name)
                continue
            if not (isinstance(rng, list) and len(rng) == 2):
                log.warning("%s: %s weight_range.%s must be [lo, hi], got %r",
                            source, race, name, rng)
                continue
            spec[idx] = (float(rng[0]) / 100.0, float(rng[1]) / 100.0)
    elif (isinstance(wr, list) and len(wr) == 3
          and all(isinstance(p, list) and len(p) == 2 for p in wr)):
        for i, (lo, hi) in enumerate(wr):
            spec[i] = (float(lo) / 100.0, float(hi) / 100.0)
    else:
        log.warning("%s: %s weight_range must be a {axis = [lo,hi]} table or "
                    "three [lo,hi] pairs, got %r", source, race, wr)
        return None
    return spec or None
