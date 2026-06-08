"""Apply FO4 chargen `.tri` shape-key morphs to head-shape vertices.

The Face head part's Chargen Morph `.tri` holds named shape keys (MPPM names:
`NoseSizeType1`, `NeckThickType1`, …) as absolute vertex positions, 1:1 with the
head shape's verts. A furrified NPC carries MSDK/MSDV (preset key + weight); the
bake applies `sum(weight * (morph - basis))` per vertex to the baked head.

PyNifly's `TriFile` lives under a package whose `__init__` imports `bpy`, so we
load `trifile.py` directly via `importlib` (the path comes from `_pyn`). Loaded
tris are cached per resolved path (per worker process).
"""

from __future__ import annotations

import importlib.util
import logging

from .._pyn import ensure_dev_path, trifile_path

log = logging.getLogger(__name__)

_TriFile = None
# resolved tri path -> _Chargen | None (None = a prior load failed, don't retry)
_cache: dict = {}


def _trifile_cls():
    global _TriFile
    if _TriFile is None:
        ensure_dev_path()
        spec = importlib.util.spec_from_file_location(
            "pyn_trifile", trifile_path())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TriFile = mod.TriFile
    return _TriFile


class _Chargen:
    """A loaded chargen tri: its basis verts + {morph-name: absolute verts}."""

    def __init__(self, path: str):
        with open(path, "rb") as f:
            tri = _trifile_cls().from_file(f)
        if tri is None:
            raise ValueError(f"could not read tri {path}")
        # `Basis` is the neutral shape; `_vertices` is the same base the reader
        # adds the per-morph deltas onto.
        self.basis = tri.morphs.get("Basis") or tri._vertices
        self.morphs = tri.morphs
        self.vert_count = len(self.basis)


def _load(path: str):
    if path not in _cache:
        try:
            _cache[path] = _Chargen(path)
        except Exception as exc:
            log.warning("chargen tri load failed (%s): %s", path, exc)
            _cache[path] = None
    return _cache[path]


def morphed_verts(base_verts: list, tri_path: str, morphs: list) -> list:
    """`base_verts` (the head shape's verts) with the chargen morphs applied:
    `v + sum(weight * (morph[v] - basis[v]))`. Returns `base_verts` unchanged on
    any problem (tri unreadable, vert-count mismatch, no morphs resolve) — the
    head simply stays race-default rather than corrupting."""
    if not morphs or not tri_path:
        return base_verts
    cm = _load(tri_path)
    if cm is None:
        return base_verts
    if len(base_verts) != cm.vert_count:
        log.warning("head shape has %d verts but chargen tri has %d; morphs "
                    "skipped", len(base_verts), cm.vert_count)
        return base_verts

    deltas = [[0.0, 0.0, 0.0] for _ in range(cm.vert_count)]
    applied = 0
    for name, weight in morphs:
        verts = cm.morphs.get(name)
        if verts is None:
            log.warning("chargen morph %r not in tri; skipped", name)
            continue
        for i in range(cm.vert_count):
            b, v, d = cm.basis[i], verts[i], deltas[i]
            d[0] += weight * (v[0] - b[0])
            d[1] += weight * (v[1] - b[1])
            d[2] += weight * (v[2] - b[2])
        applied += 1
    if not applied:
        return base_verts
    return [(bv[0] + d[0], bv[1] + d[1], bv[2] + d[2])
            for bv, d in zip(base_verts, deltas)]
