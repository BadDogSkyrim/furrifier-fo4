"""Deterministic hashing and small helpers.

The hash is the same deterministic function the Skyrim furrifier uses, so a
given NPC signature + seed always yields the same pick across runs — the
project-wide determinism invariant (PLAN_FO4_SCHEME.md, line 7).
"""


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
