"""Catalog validation: warn at load when a race_customization references a
facemorph preset/region or a tint color the engine RACE doesn't actually offer
— with a nearest-name suggestion.

Both classes of mistake we kept hitting were the same shape: a name in the
catalog (`FFOFurTan` skin tone, `CougarType1` on a female lion) that the race's
real data doesn't provide, found only via a runtime "skipped" warning or a
silently-dropped layer. This turns those into load-time warnings with a "did
you mean …?" against what the race actually offers.

Pure: takes the already-loaded indexes (RaceMorphs / RaceTints / FacialBone-
Regions) and a Customization, returns the warning strings (also logged).
"""

from __future__ import annotations

import logging
from difflib import get_close_matches
from typing import Optional

from .models import Sex
from .tints import resolve_options

log = logging.getLogger(__name__)

_SEXES = (Sex.MALE, Sex.FEMALE)


def _sexes_for(block_sex: Optional[str]) -> tuple:
    """A block's sex ('male'/'female'/None=both) -> the sexes it applies to."""
    if block_sex == 'female':
        return (Sex.FEMALE,)
    if block_sex == 'male':
        return (Sex.MALE,)
    return _SEXES


def _sx(sex: Sex) -> str:
    return 'female' if sex == Sex.FEMALE else 'male'


def _suggest(name: str, valid) -> str:
    """' (did you mean 'x'?)' for the closest valid name, or '' if none close.
    ASCII only — the log pane and Windows console mangle non-ASCII."""
    m = get_close_matches(name.lower(), [v.lower() for v in valid],
                          n=1, cutoff=0.4)
    return f" (did you mean {m[0]!r}?)" if m else ''


def validate_customization(cust, race_morphs, race_tints) -> list:
    """Check every facemorph + color reference in `cust` against the real race
    data. Returns a list of warning strings (each also logged at WARNING)."""
    warnings: list = []
    seen: set = set()

    def warn(msg: str) -> None:
        # Dedup identical messages — a palette repeats a color, and one scheme/
        # spec is referenced by several breeds; all collapse to one line.
        if msg in seen:
            return
        seen.add(msg)
        warnings.append(msg)
        log.warning("catalog: %s", msg)

    def engine_race(ref: str) -> str:
        # A breed's morph/tint indices come from its parent engine RACE.
        breed = cust.breeds.get(ref)
        return breed.parent_race_edid if breed is not None else ref

    _validate_facemorphs(cust, race_morphs, engine_race, warn)
    _validate_tints(cust, race_tints, engine_race, warn)
    return warnings


def _validate_facemorphs(cust, race_morphs, engine_race, warn):
    for ref, spec_name in cust.facemorph_refs.items():
        spec = cust.facemorphs.get(spec_name)
        if spec is None:
            warn(f"facemorphs: {ref} references unknown spec {spec_name!r}")
            continue
        race = engine_race(ref)
        for region in spec.regions:
            for sex in _sexes_for(region.sex):
                _check_region(spec_name, race, sex, region, race_morphs, warn)
        for g in spec.groups:
            for sex in _sexes_for(g.sex):
                if race_morphs.mppi_for(race, sex, g.group, g.preset) is None:
                    presets = race_morphs.groups_for(race, sex).get(
                        g.group.lower(), [])
                    warn(f"facemorphs {spec_name!r}: {_sx(sex)} preset "
                         f"{g.preset!r} not in group {g.group!r} for {race}"
                         f"{_suggest(g.preset, presets)}")


def _check_region(spec_name, race, sex, region, race_morphs, warn):
    if region.has_transform() and \
            race_morphs.fmri_for(race, sex, region.name) is None:
        warn(f"facemorphs {spec_name!r}: {_sx(sex)} region {region.name!r} not "
             f"found for {race}"
             f"{_suggest(region.name, race_morphs.regions_for(race, sex))}")
    if not region.presets:
        return
    # The region key doubles as the morph-group name — resolve presets straight
    # from the RACE record (no external region->group asset).
    group = region.name
    groups = race_morphs.groups_for(race, sex)
    all_presets = sorted({p for ps in groups.values() for p in ps})
    for preset, _weight in region.presets:
        if race_morphs.mppi_for(race, sex, group, preset) is not None:
            continue
        # Most useful hint: which group actually holds this preset? For the nose
        # the region name and its group name differ (region 'Nose - Bridge' vs
        # group 'Nose Shape'), so a preset keyed on the region can't resolve —
        # name the owning group instead.
        owning = next((g for g, ps in groups.items()
                       if preset.lower() in (p.lower() for p in ps)), None)
        if owning is not None:
            warn(f"facemorphs {spec_name!r}: {_sx(sex)} preset {preset!r} is in "
                 f"group {owning!r}, not {group!r}, for {race}; key the entry on "
                 f"the group name instead")
        else:
            warn(f"facemorphs {spec_name!r}: {_sx(sex)} preset {preset!r} does "
                 f"not exist for {race}{_suggest(preset, all_presets)}")


def _validate_tints(cust, race_tints, engine_race, warn):
    for ref, scheme_name in cust.colors.items():
        scheme = cust.color_schemes.get(scheme_name)
        if not scheme:
            continue
        race = engine_race(ref)
        cats = cust.categories_for(race)
        for block_sex, rules in scheme.items():
            for sex in _sexes_for(block_sex):
                names = race_tints.options_by_name(race, sex)
                for cat, rule in rules.items():
                    opts = resolve_options(cats, names, cat.lower())
                    if not opts:
                        warn(f"color_scheme {scheme_name!r}: category {cat!r} "
                             f"matches no tint layer for {race} ({_sx(sex)})")
                        continue
                    if not rule.colors:
                        continue
                    offered = {race_tints.clfm_edid(fid)
                               for o in opts for fid, _a, _t in o.colors}
                    offered.discard(None)
                    for color, _intensity in rule.colors:
                        if color not in offered:
                            warn(f"color_scheme {scheme_name!r}: {_sx(sex)} "
                                 f"{cat!r} for {race} doesn't offer color "
                                 f"{color!r}{_suggest(color, offered)}")
