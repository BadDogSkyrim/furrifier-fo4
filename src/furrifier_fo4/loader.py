"""Parse scheme + built-in TOML into a Scheme object.

Format spec: PLAN_FO4_SCHEME.md. Built-in definitions (class_match, aliases,
families) live in a `builtin.toml`; a scheme file under `schemes/` supplies
class_probabilities, npc_assignments, and optional extra class_match rules
that are *prepended* to the built-ins (scheme rules win).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Optional

from .models import ClassDistribution, MatchRule, WeightedRace, MATCH_FIELDS
from .scheme import Scheme


class SchemeError(ValueError):
    """A scheme or built-in TOML file is malformed."""


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

    # class_match: scheme rules FIRST (higher priority), then built-ins.
    scheme.class_rules = (
        _parse_class_match(scheme_data.get('class_match', []), 'scheme')
        + _parse_class_match(builtin_data.get('class_match', []), 'builtin'))

    # aliases: [[first, id, id, ...], ...]; first entry is the signature.
    for row in builtin_data.get('aliases', []):
        if not row:
            continue
        scheme.aliases[row[0]] = list(row)

    # families: [[leader, member, ...], ...]
    scheme.families = [list(fam) for fam in builtin_data.get('families', [])
                       if fam]

    # distributions + per-NPC assignments come from the scheme file.
    scheme.distributions = _parse_distributions(
        scheme_data.get('class_probabilities', []), 'scheme')
    for edid, race in scheme_data.get('npc_assignments', {}).items():
        scheme.npc_assignments[edid] = str(race)

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

    return parse_scheme(builtin_data, scheme_data)
