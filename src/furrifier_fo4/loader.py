"""Parse scheme + built-in TOML into a Scheme object.

Format spec: PLAN_FO4_SCHEME.md. Built-in definitions (class_match, aliases,
families) live in a `builtin.toml`; a scheme file under `schemes/` supplies
class_probabilities, npc_assignments, and may also EXTEND any built-in section
— extra class_match rules, aliases, and families — so a scheme can support a
mod's NPCs without editing builtin.toml. In every case the scheme wins over a
built-in on collision (class_match rules are prepended; alias signatures and
family members are overridden).
"""

from __future__ import annotations

import logging
import sys
import tomllib
from pathlib import Path
from typing import Optional

from .models import ClassDistribution, MatchRule, WeightedRace, MATCH_FIELDS
from .scheme import Scheme

log = logging.getLogger(__name__)

# The only top-level keys each file may define. A key outside the set is almost
# always a typo or a section in the wrong file (e.g. [[facemorphs]], which
# belongs in a race catalog, not a scheme) — warn instead of silently dropping
# the whole section.
# A scheme may define anything the built-in file can (class_match, aliases,
# families) so it can support a mod's content without editing builtin.toml, plus
# the scheme-only keys (probabilities, assignments, exclusions).
_SCHEME_KEYS = {'class_match', 'aliases', 'families', 'class_probabilities',
                'npc_assignments', 'exclude_headparts'}
_BUILTIN_KEYS = {'class_match', 'aliases', 'families'}


class SchemeError(ValueError):
    """A scheme or built-in TOML file is malformed."""


def _lint_top_level(data: dict, allowed: set, source: str) -> None:
    for key in data:
        if key not in allowed:
            log.warning("%s: unrecognized top-level key %r - ignored "
                        "(expected one of %s)", source, key,
                        ', '.join(sorted(allowed)))


def _find_resource_dir(name: str) -> Optional[Path]:
    """Locate a top-level resource dir (schemes/ or builtin) in frozen or
    dev mode. Frozen: next to the exe. Dev: nearest ancestor with the name.
    """
    if getattr(sys, 'frozen', False):
        p = Path(sys.executable).parent / name
        return p if p.exists() else None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / name
        if candidate.exists():
            return candidate
    return None


def _parse_class_match(rows, source: str) -> list[MatchRule]:
    """Parse a class_match = [[class, field, pattern], ...] table."""
    rules: list[MatchRule] = []
    for i, row in enumerate(rows):
        where = f"{source} class_match[{i}]"
        if not isinstance(row, list) or len(row) != 3:
            raise SchemeError(
                f"{where}: expected [class, field, pattern], got {row!r}")
        cls, field, pattern = row
        field_l = str(field).lower()
        if field_l not in MATCH_FIELDS:
            raise SchemeError(
                f"{where}: unknown field {field!r}; "
                f"expected one of {MATCH_FIELDS}")
        rules.append(MatchRule(class_name=str(cls), field=field_l,
                               pattern=str(pattern)))
    return rules


def _parse_id_lists(rows, source: str, key: str) -> list[list[str]]:
    """Parse an aliases/families table: a list of [first, member, ...] rows,
    each with at least two EditorIDs. Used for both `aliases` (first entry is
    the signature) and `families` (first entry is the leader)."""
    out: list[list[str]] = []
    for i, row in enumerate(rows):
        where = f"{source} {key}[{i}]"
        if not isinstance(row, list) or len(row) < 2:
            raise SchemeError(
                f"{where}: expected [first, member, ...] with at least two "
                f"entries, got {row!r}")
        out.append([str(x) for x in row])
    return out


def _parse_distributions(rows, source: str) -> dict[str, ClassDistribution]:
    """Parse [[class_probabilities]] blocks into per-class distributions."""
    dists: dict[str, ClassDistribution] = {}
    for i, block in enumerate(rows):
        where = f"{source} class_probabilities[{i}]"
        cls = block.get('class')
        if not cls:
            raise SchemeError(f"{where}: missing 'class'")
        races = []
        for j, entry in enumerate(block.get('races', [])):
            if not isinstance(entry, list) or len(entry) != 2:
                raise SchemeError(
                    f"{where}.races[{j}]: expected [race, weight], "
                    f"got {entry!r}")
            race, weight = entry
            races.append(WeightedRace(race=str(race), weight=int(weight)))
        if cls in dists:
            raise SchemeError(f"{where}: duplicate class {cls!r}")
        dists[str(cls)] = ClassDistribution(class_name=str(cls), races=races)
    return dists


def parse_scheme(builtin_data: dict, scheme_data: dict) -> Scheme:
    """Build a Scheme from already-loaded built-in and scheme dicts.

    Scheme class_match rules are prepended to the built-ins so they take
    priority (PLAN_FO4_SCHEME.md line 124).
    """
    scheme = Scheme()

    # A scheme may extend every built-in section so it can cover a mod's NPCs
    # without editing builtin.toml. In all three the scheme takes priority over
    # a built-in on collision.

    # class_match: scheme rules FIRST (first-match-wins => higher priority).
    scheme.class_rules = (
        _parse_class_match(scheme_data.get('class_match', []), 'scheme')
        + _parse_class_match(builtin_data.get('class_match', []), 'builtin'))

    # aliases: [[signature, id, ...], ...]. Built-ins loaded first, then scheme
    # entries, so a scheme row with the same signature overrides the built-in.
    for row in (_parse_id_lists(builtin_data.get('aliases', []), 'builtin', 'aliases')
                + _parse_id_lists(scheme_data.get('aliases', []), 'scheme', 'aliases')):
        scheme.aliases[row[0]] = row

    # families: [[leader, member, ...], ...]. Scheme families appended after the
    # built-ins; on a shared member, build_indexes' last write (the scheme's)
    # wins, and reusing a built-in leader as the first entry extends that family.
    scheme.families = (
        _parse_id_lists(builtin_data.get('families', []), 'builtin', 'families')
        + _parse_id_lists(scheme_data.get('families', []), 'scheme', 'families'))

    # distributions + per-NPC assignments come from the scheme file.
    scheme.distributions = _parse_distributions(
        scheme_data.get('class_probabilities', []), 'scheme')
    for edid, race in scheme_data.get('npc_assignments', {}).items():
        scheme.npc_assignments[edid] = str(race)

    # Headparts the scheme never wants applied (exact EDID, case-insensitive).
    scheme.exclude_headparts = {
        str(e).lower() for e in scheme_data.get('exclude_headparts', [])}

    scheme.build_indexes()
    _validate(scheme)
    return scheme


def _validate(scheme: Scheme) -> None:
    """Cheap sanity checks that catch common authoring mistakes."""
    # A family member must not also be an NPC EditorID used as a family
    # name elsewhere — but families are positional lists, so the only
    # collision risk is an alias member also being a family leader. Warn-
    # level checks are left to the session; here we just ensure weights are
    # non-negative and at least one positive per class.
    for cls, dist in scheme.distributions.items():
        if any(wr.weight < 0 for wr in dist.races):
            raise SchemeError(f"class {cls!r}: negative weight not allowed")
        if dist.total_weight <= 0:
            raise SchemeError(
                f"class {cls!r}: total weight must be > 0 "
                f"(got {dist.total_weight})")


def scheme_source_paths(scheme_name: str) -> list[Path]:
    """The TOML files `load_scheme` reads for `scheme_name`: builtin.toml and
    schemes/<name>.toml. Used to detect edits for cache invalidation (the scheme
    file paths it returns may not exist yet — the caller stats them)."""
    paths: list[Path] = []
    builtin = _find_resource_dir('builtin.toml')
    if builtin is not None:
        paths.append(builtin)
    schemes_dir = _find_resource_dir('schemes')
    if schemes_dir is not None:
        paths.append(schemes_dir / f"{scheme_name}.toml")
    return paths


def list_available_schemes() -> list[str]:
    """Stem names of scheme TOMLs in the shipped schemes/ dir (lowercased)."""
    d = _find_resource_dir('schemes')
    if d is None or not d.is_dir():
        return []
    return sorted(p.stem.lower() for p in d.glob('*.toml'))


def load_scheme(scheme_name: str) -> Scheme:
    """Load builtin.toml + schemes/<name>.toml from disk into a Scheme."""
    builtin_path = _find_resource_dir('builtin.toml')
    builtin_data = {}
    if builtin_path is not None and builtin_path.is_file():
        with open(builtin_path, 'rb') as f:
            builtin_data = tomllib.load(f)
        _lint_top_level(builtin_data, _BUILTIN_KEYS, builtin_path.name)

    schemes_dir = _find_resource_dir('schemes')
    if schemes_dir is None:
        raise SchemeError(
            "Could not locate schemes/ directory next to the executable "
            "or in the project tree.")
    scheme_path = schemes_dir / f"{scheme_name}.toml"
    if not scheme_path.is_file():
        available = ', '.join(list_available_schemes()) or '(none)'
        raise SchemeError(
            f"Scheme {scheme_name!r} not found in {schemes_dir}. "
            f"Available: {available}")
    with open(scheme_path, 'rb') as f:
        scheme_data = tomllib.load(f)
    _lint_top_level(scheme_data, _SCHEME_KEYS, scheme_path.name)

    return parse_scheme(builtin_data, scheme_data)
