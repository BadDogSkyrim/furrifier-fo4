"""Deterministic hashing and small helpers.

The hash is the same deterministic function the Skyrim furrifier uses, so a
given NPC signature + seed always yields the same pick across runs — the
project-wide determinism invariant (PLAN_FO4_SCHEME.md, line 7).
"""

import logging

log = logging.getLogger(__name__)


def hash_string(s: str, seed: int, m: int) -> int:
    """Hash a string with seed, return result modulo m.

    Deterministic pseudo-random selection: different seeds decorrelate
    independent choices for the same NPC (race vs headpart vs tint).
    """
    h = seed
    for c in s:
        h = ((31 * h) + ord(c)) % 16000
    h = (31 * h) % 16000
    if m == 0:
        return 0
    return h % m


# Seed for range picks. Distinct from the race/headpart/tint/weight seeds so a
# ranged value doesn't correlate with the choices made for the same NPC.
_RANGE_SEED = 7717


def parse_range(val, ctx: str = '', default=None):
    """Normalize a catalog scalar to a `(lo, hi)` range.

    Every number in a catalog may be written either bare (`0.5`) or as a
    two-element range (`[0.2, 0.8]`) that each NPC draws its own value
    from. A bare number is the degenerate range `(0.5, 0.5)`, so callers
    resolve both forms through one path -- there is no "is it ranged?"
    branch anywhere downstream.

    Returns `default` (and warns, if `ctx` is given) for anything that is
    neither. `lo > hi` is accepted and swapped.
    """
    if isinstance(val, bool):           # bool is an int subclass; not a scalar
        pass
    elif isinstance(val, (int, float)):
        return (float(val), float(val))
    elif _is_number_pair(val):
        lo, hi = float(val[0]), float(val[1])
        return (lo, hi) if lo <= hi else (hi, lo)
    if ctx:
        log.warning("%s: expected a number or a [lo, hi] range, got %r; "
                    "ignored", ctx, val)
    return default


def parse_probability(val, ctx: str = '', default=None):
    """A catalog probability: a plain number, never a range.

    Ranges belong on values written ONTO the NPC (`parse_range`), not on
    probabilities -- drawing a random probability per NPC and then rolling
    against it is distributionally identical to rolling against the
    midpoint, so a range here would be a knob that does nothing. Reject it
    with a message naming the number to use instead, rather than crash on
    `float(a_list)` or silently accept it.

    Returns `default` (warning, if `ctx` is given) for anything else.
    """
    if isinstance(val, bool):           # bool is an int subclass; not a number
        pass
    elif isinstance(val, (int, float)):
        return float(val)
    elif ctx and _is_number_pair(val):
        log.warning("%s: a probability takes a plain number, not a range -- "
                    "%r would behave exactly like %g; use that instead. Ignored",
                    ctx, list(val), (float(val[0]) + float(val[1])) / 2)
        return default
    if ctx:
        log.warning("%s: expected a probability (a number), got %r; ignored",
                    ctx, val)
    return default


def _is_number_pair(val) -> bool:
    return (isinstance(val, (list, tuple)) and len(val) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in val))


def pick_range(rng, signature: str, key: str = '') -> float:
    """Draw this NPC's value from a `(lo, hi)` range.

    Deterministic on the NPC's `signature` (the project-wide invariant), so
    a run always reproduces. `key` names the field being drawn -- two
    ranges on the same NPC decorrelate only if their keys differ, so pass
    something field-specific (`"Eyes.scale"`, not `"scale"`).
    """
    lo, hi = rng
    if lo == hi:
        return lo
    frac = hash_string(f"{signature}|{key}", _RANGE_SEED, 1001) / 1000.0
    return lo + frac * (hi - lo)


def wildcard_match(pattern: str, value: str) -> bool:
    """Case-insensitive match with '*' allowed at the start and/or end.

    '*Minutemen*' -> contains, 'Settler*' -> startswith,
    '*Corpse' -> endswith, 'Exact' -> equals. A bare '*' matches anything.
    """
    p = pattern.lower()
    v = value.lower()
    star_start = p.startswith('*')
    star_end = p.endswith('*')
    core = p.strip('*')
    if not core:
        return True  # '*' or '**'
    if star_start and star_end:
        return core in v
    if star_start:
        return v.endswith(core)
    if star_end:
        return v.startswith(core)
    return v == core
