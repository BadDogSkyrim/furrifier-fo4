"""Deterministic checks on the FO4 tint blend operations.

These pin the exact blend math the CK throws away. Values are computed by
hand from the standard formulas so a regression in any kernel is caught.
"""

import numpy as np
import pytest

from furrifier_fo4.facegen import blend


def _b(v):
    return np.full((1, 1, 3), v, dtype=np.float32)


def test_default_is_soft_light():
    # FO4 "Default" tint blend = soft light (base-preserving), not a replace.
    # base=0.3, src=0.7 (s>=0.5): 2*0.3*(1-0.7) + sqrt(0.3)*(2*0.7-1)
    #   = 0.18 + 0.547722*0.4 = 0.399089
    out = blend.blend(blend.DEFAULT, _b(0.3), [0.7, 0.7, 0.7])
    assert out == pytest.approx(0.399089, abs=1e-5)


def test_multiply():
    out = blend.blend(blend.MULTIPLY, _b(0.5), 0.5)
    assert out == pytest.approx(0.25)


def test_overlay_dark_base():
    # base < 0.5 -> 2*base*src
    out = blend.blend(blend.OVERLAY, _b(0.25), 0.6)
    assert out == pytest.approx(0.30)


def test_overlay_light_base():
    # base >= 0.5 -> 1 - 2*(1-base)*(1-src)
    out = blend.blend(blend.OVERLAY, _b(0.75), 0.6)
    assert out == pytest.approx(0.80)


def test_hard_light_dark_source():
    # src < 0.5 -> 2*base*src   (overlay with base/src swapped)
    out = blend.blend(blend.HARD_LIGHT, _b(0.6), 0.25)
    assert out == pytest.approx(0.30)


def test_hard_light_light_source():
    out = blend.blend(blend.HARD_LIGHT, _b(0.6), 0.75)
    assert out == pytest.approx(0.80)


def test_soft_light_dark_source():
    # s < 0.5 -> 2*b*s + b*b*(1-2s); b=0.5,s=0.25 -> 0.25 + 0.25*0.5 = 0.375
    out = blend.blend(blend.SOFT_LIGHT, _b(0.5), 0.25)
    assert out == pytest.approx(0.375)


def test_unknown_op_falls_back_to_default():
    # Falls back to Default = soft light (base-preserving), not a replace.
    out = blend.blend(99, _b(0.3), 0.7)
    assert out == pytest.approx(0.399089, abs=1e-5)
