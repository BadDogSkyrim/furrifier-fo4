"""Compositor checks for the FaceCustomization diffuse bake.

Tints composite onto a base head diffuse (a tiny on-disk PNG here); mask
coverage is pre-seeded into `resolver.image_cache` so no mask files are read.
"""

import numpy as np
from PIL import Image

from furrifier_fo4.facegen import blend
from furrifier_fo4.facegen.composite import composite_diffuse


class _FakeResolver:
    """Resolves only the base diffuse (to a real temp path); masks come from
    the pre-seeded image_cache."""

    def __init__(self, base_path):
        self.image_cache = {}
        self._base_path = base_path

    def resolve(self, relpath):
        return self._base_path if relpath == "BASE" else None


def _seed(resolver, name, size, value):
    key = (name.replace("/", "\\").lower(), size)
    resolver.image_cache[key] = np.full((size, size), value, dtype=np.float32)


def _base_png(tmp_path, size, rgb):
    p = tmp_path / "base.png"
    Image.fromarray(np.full((size, size, 3), rgb, dtype=np.uint8), "RGB").save(p)
    return p


def test_multiply_layer_onto_base(tmp_path):
    r = _FakeResolver(_base_png(tmp_path, 2, [100, 200, 50]))
    _seed(r, "m", 2, 1.0)  # full coverage
    layers = [{"mask": "m", "color": [255, 0, 0], "intensity": 1.0,
               "blend": blend.MULTIPLY, "is_skin_tone": False}]
    out = composite_diffuse(r, "BASE", layers, output_size=2)
    # base * (1,0,0): red channel kept, green/blue zeroed.
    px = out[0, 0]
    assert abs(float(px[0]) - 100 / 255) < 1e-3
    assert float(px[1]) == 0.0
    assert float(px[2]) == 0.0


def test_default_soft_light_partial_coverage(tmp_path):
    # Default = soft light (base-preserving). base=0.6, white src:
    #   softlight(0.6,1.0) = sqrt(0.6) = 0.774597; at coverage 0.5:
    #   0.6 + 0.5*(0.774597-0.6) = 0.687298  (NOT a flat 0.5 replace).
    r = _FakeResolver(_base_png(tmp_path, 2, [153, 153, 153]))  # 0.6
    _seed(r, "m", 2, 0.5)  # half coverage
    layers = [{"mask": "m", "color": [255, 255, 255], "intensity": 1.0,
               "blend": blend.DEFAULT, "is_skin_tone": False}]
    out = composite_diffuse(r, "BASE", layers, output_size=2)
    assert abs(float(out[0, 0, 0]) - 0.687298) < 1e-3


def test_missing_mask_keeps_base(tmp_path):
    r = _FakeResolver(_base_png(tmp_path, 2, [10, 20, 30]))  # nothing seeded
    layers = [{"mask": "missing", "color": [255, 0, 0], "intensity": 1.0,
               "blend": blend.DEFAULT, "is_skin_tone": False}]
    out = composite_diffuse(r, "BASE", layers, output_size=2)
    px = out[0, 0]
    assert abs(float(px[0]) - 10 / 255) < 1e-3
    assert abs(float(px[1]) - 20 / 255) < 1e-3
