"""Bake FO4 facebone (region) morphs into head vertices via linear-blend skinning.

A region (FMRI) moves one or more facegen bones; the FMRS sliders (-1..1) pick
each bone's transform between the region JSON's `Minima` (-1) and `Maxima` (+1),
through the rest (0). The head's *facebones* mesh (`<head>_faceBones.nif`, same
verts as the head, skinned to `skin_bone_*`) is what the CK deforms — so we
replicate its LBS and add the resulting per-vertex displacement to the baked
head shape.

For a bone with local delta `D` (bone-local space), skin->bone xform `S` (from
the facebones nif), a weighted vertex moves by `w * (S^-1 · D · S · v - v)`.

CALIBRATION KNOBS (FO4 conventions, verify against a CK bake):
  - slider -> value: piecewise-linear through rest 0 (`s*max` / `-s*min`).
  - rotation: JSON degrees -> radians; Euler order Rx·Ry·Rz (intrinsic).
  - scale: factor = 1 + interpolated scale offset (rest 0 -> x1).
  - compose: D = Translate · Rotate · Scale (bone-local).
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# JSON facebone names are bare (`bone_C_MasterNose`); the facebones nif skins to
# the `skin_`-prefixed form.
_SKIN_PREFIX = "skin_"


# resolved facebones-nif path -> its largest shape (the head), cached per worker.
_shape_cache: dict = {}
# resolved skeleton path -> FaceboneSkeleton, cached per worker.
_skel_cache: dict = {}

# The facebone skeleton (full bone hierarchy + bind transforms, incl. the
# control bones a region can drive that the head isn't directly skinned to).
SKELETON_FACEBONES = ("meshes", "actors", "character", "characterassets",
                      "skeleton_facebones.nif")


def _nif_file(path: str):
    from .._pyn import ensure_dev_path
    ensure_dev_path()
    from pyn.pynifly import NifFile
    return NifFile(path)


def load_facebones_shape(path: str):
    """The facebones nif's head shape (max verts), cached. None on failure."""
    if path not in _shape_cache:
        try:
            nif = _nif_file(path)
            _shape_cache[path] = max(nif.shapes, key=lambda s: len(s.verts),
                                     default=None)
        except Exception as exc:
            log.warning("facebones nif load failed (%s): %s", path, exc)
            _shape_cache[path] = None
    return _shape_cache[path]


class FaceboneSkeleton:
    """The facebone skeleton's bone tree: each bone's WORLD bind transform (4x4,
    skeleton space — composed from the local node transforms) and its children.
    Used to drive a head skin bone from one of its ANCESTOR control bones (e.g.
    region `skin_bone_L_Ear` -> the head's `skin_bone_L_EarTop`)."""

    def __init__(self, path: str):
        from collections import defaultdict
        nif = _nif_file(path)
        local: dict = {}
        parent: dict = {}
        self.children = defaultdict(list)
        for name, node in nif.nodes.items():
            local[name] = _s2b_matrix(node.transform)
            p = node.parent
            parent[name] = p.name if p is not None else None
            if p is not None:
                self.children[p.name].append(name)
        self.world: dict = {}

        def world_of(name):
            if name in self.world:
                return self.world[name]
            p = parent.get(name)
            w = local[name] if not p else world_of(p) @ local[name]
            self.world[name] = w
            return w

        for name in nif.nodes:
            world_of(name)

    def descendants(self, bone: str):
        out, stack = [], list(self.children.get(bone, []))
        while stack:
            b = stack.pop()
            out.append(b)
            stack.extend(self.children.get(b, []))
        return out


def load_facebone_skeleton(path: str):
    """A FaceboneSkeleton, cached per resolved path. None on failure."""
    if path not in _skel_cache:
        try:
            _skel_cache[path] = FaceboneSkeleton(path)
        except Exception as exc:
            log.warning("facebone skeleton load failed (%s): %s", path, exc)
            _skel_cache[path] = None
    return _skel_cache[path]


def _interp(s: float, mn: float, mx: float) -> float:
    """Slider `s` in -1..1 between `mn` (-1) and `mx` (+1) through rest 0."""
    return s * mx if s >= 0.0 else -s * mn


def _axis(d: dict, k: str):
    v = d.get(k) or {}
    return (float(v.get("x", 0.0)), float(v.get("y", 0.0)), float(v.get("z", 0.0)))


def bone_delta_matrix(minima: dict, maxima: dict, fmrs) -> np.ndarray:
    """The 4x4 bone-local delta transform for one bone, from its Minima/Maxima
    and the NPC's 7 FMRS sliders (posX/Y/Z, rotX/Y/Z, scale)."""
    px, py, pz, rx, ry, rz, sc = fmrs[:7]
    mnP, mxP = _axis(minima, "Position"), _axis(maxima, "Position")
    mnR, mxR = _axis(minima, "Rotation"), _axis(maxima, "Rotation")
    mnS, mxS = _axis(minima, "Scale"), _axis(maxima, "Scale")

    tx = _interp(px, mnP[0], mxP[0])
    ty = _interp(py, mnP[1], mxP[1])
    tz = _interp(pz, mnP[2], mxP[2])
    ax = math.radians(_interp(rx, mnR[0], mxR[0]))
    ay = math.radians(_interp(ry, mnR[1], mxR[1]))
    az = math.radians(_interp(rz, mnR[2], mxR[2]))
    # One scale slider drives all axes (game/CK); JSON stores x/y/z, use x.
    scale = 1.0 + _interp(sc, mnS[0], mxS[0])

    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    R = Rx @ Ry @ Rz

    D = np.eye(4)
    D[:3, :3] = R * scale
    D[:3, 3] = (tx, ty, tz)
    return D


def _s2b_matrix(s2b) -> np.ndarray:
    """4x4 from a PyNifly skin->bone TransformBuf (rotation · scale | translation)."""
    M = np.eye(4)
    rot = np.array([[float(c) for c in row] for row in s2b.rotation])
    M[:3, :3] = rot * float(getattr(s2b, "scale", 1.0) or 1.0)
    t = s2b.translation
    M[:3, 3] = (float(t[0]), float(t[1]), float(t[2]))
    return M


def _bind_in_head_space(shape, bare: str, skeleton):
    """The 4x4 bind transform (head skin space) of the region bone named `bare`
    (`skin_<bare>`), plus the head skin bones it moves.

    If the head is directly skinned to it, that's `inv(skin->bone)` on its own
    verts. Otherwise it's an ancestor control bone (e.g. `skin_bone_L_Ear`) — the
    head skins to a descendant S, so derive its head-space bind from S via the
    skeleton's frame-independent local relation: `B_head = S_head · S_skel⁻¹ ·
    B_skel`, and move all skinned descendants. (None, []) if nothing resolves.
    """
    head_skin = set(shape.bone_names)
    cands = (_SKIN_PREFIX + bare, bare)
    bone = next((c for c in cands if c in head_skin), None)
    if bone is not None:                       # head skins to it directly
        affected = [bone]
        if skeleton is not None:               # +any skinned descendants riding it
            affected += [d for d in skeleton.descendants(bone)
                         if d in head_skin]
        B_head = np.linalg.inv(_s2b_matrix(shape.get_shape_skin_to_bone(bone)))
        return B_head, affected
    if skeleton is None:
        return None, []
    bone = next((c for c in cands if skeleton.world.get(c) is not None), None)
    if bone is None:
        return None, []
    affected = [d for d in skeleton.descendants(bone) if d in head_skin]
    if not affected:
        return None, []
    s = affected[0]
    S_head = np.linalg.inv(_s2b_matrix(shape.get_shape_skin_to_bone(s)))
    B_head = S_head @ np.linalg.inv(skeleton.world[s]) @ skeleton.world[bone]
    return B_head, affected


def facebone_displacements(shape, deltas: dict, skeleton=None) -> np.ndarray:
    """Per-vertex displacement (N,3) on a facebones nif `shape` for `deltas`
    ({bare-bone-name: 4x4 bone-local delta}).

    A region bone is `skin_<bare>`. If the head skins to it, its weighted verts
    move by `w·(B_head·D·B_head⁻¹·v − v)`. If it's an ancestor control bone (the
    head skins to a descendant), the same world-space delta is applied to the
    descendant's verts (needs the `skeleton` for the bind relation). The result
    adds to the head shape's verts (shared order)."""
    verts = np.asarray(list(shape.verts), dtype=float)
    n = len(verts)
    disp = np.zeros((n, 3))
    homog = np.empty((n, 4))
    homog[:, :3] = verts
    homog[:, 3] = 1.0
    for bare, D in deltas.items():
        B_head, affected = _bind_in_head_space(shape, bare, skeleton)
        if B_head is None:
            log.warning("facebone %r: not a head skin bone and no skinned "
                        "descendant in the facebone skeleton; region skipped",
                        bare)
            continue
        M = B_head @ np.asarray(D) @ np.linalg.inv(B_head)
        moved = (homog @ M.T)[:, :3] - verts          # per-vert full displacement
        for nif_bone in affected:
            for i, w in shape.bone_weights[nif_bone]:
                if w > 0.0:
                    disp[i] += w * moved[i]
    return disp
