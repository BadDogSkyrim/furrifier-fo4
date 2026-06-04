"""FO4 tint-layer blend operations — the part the CK bakes flat.

FO4 stores a Blend Operation per tint option (RACE TTEB) and per template
color (TTEC trailing u32). The enum is:

    0 Default      soft light (NOT a flat replace — see below)
    1 Multiply     base * source
    2 Overlay      base<0.5 ? 2*base*src : 1-2*(1-base)*(1-src)
    3 Soft Light   Photoshop soft light
    4 Hard Light   overlay with base/source swapped

"Default" is the op FFO authors on the skin-tone layer (e.g. CheetahRace skin
tone over a full-face mask at intensity 1). It is NOT a straight replace: the
engine/CK blends it over the base head diffuse so the fur pattern (a cheetah's
spots) shows through tinted. A flat replace wiped that pattern; matching the
in-game look, Default behaves as **Soft Light** (Hugh, verified against
MacCready in-game). Each blend function returns the *blended source colour*
for the layer, which the compositor then alpha-composites onto the accumulator
using the layer's coverage*intensity as opacity.

All functions take float arrays in [0,1]. `base` is the accumulated lower
layers (H,W,3); `src` is the layer colour, broadcastable to base (a (3,)
constant or an (H,W,3) array). They never clip to allow the caller to do a
single clip at the end, but every formula here is already range-safe for
inputs in [0,1].
"""

from __future__ import annotations

import numpy as np

DEFAULT = 0
MULTIPLY = 1
OVERLAY = 2
SOFT_LIGHT = 3
HARD_LIGHT = 4

NAMES = {
    DEFAULT: 'Default', MULTIPLY: 'Multiply', OVERLAY: 'Overlay',
    SOFT_LIGHT: 'Soft Light', HARD_LIGHT: 'Hard Light',
}


def _multiply(base: np.ndarray, src) -> np.ndarray:
    return base * np.asarray(src, dtype=np.float32)


def _overlay(base: np.ndarray, src) -> np.ndarray:
    s = np.asarray(src, dtype=np.float32)
    lo = 2.0 * base * s
    hi = 1.0 - 2.0 * (1.0 - base) * (1.0 - s)
    return np.where(base < 0.5, lo, hi).astype(np.float32)


def _hard_light(base: np.ndarray, src) -> np.ndarray:
    # Hard light is overlay with base and source swapped.
    s = np.asarray(src, dtype=np.float32)
    lo = 2.0 * base * s
    hi = 1.0 - 2.0 * (1.0 - base) * (1.0 - s)
    return np.where(s < 0.5, lo, hi).astype(np.float32)


def _soft_light(base: np.ndarray, src) -> np.ndarray:
    # Photoshop soft light (the common piecewise form).
    s = np.asarray(src, dtype=np.float32)
    lo = 2.0 * base * s + base * base * (1.0 - 2.0 * s)
    hi = 2.0 * base * (1.0 - s) + np.sqrt(np.clip(base, 0.0, 1.0)) * (2.0 * s - 1.0)
    return np.where(s < 0.5, lo, hi).astype(np.float32)


_OPS = {
    DEFAULT: _soft_light,   # FO4 "Default" tint blend = soft light (see module docstring)
    MULTIPLY: _multiply,
    OVERLAY: _overlay,
    SOFT_LIGHT: _soft_light,
    HARD_LIGHT: _hard_light,
}


def blend(op: int, base: np.ndarray, src) -> np.ndarray:
    """Return the blended source colour for blend op `op`.

    Unknown ops fall back to Default (soft light) — a base-preserving tint, so
    an unrecognised op never wipes the underlying fur pattern.
    """
    return _OPS.get(int(op), _soft_light)(base, src)
