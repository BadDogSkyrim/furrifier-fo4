"""Unit tests for tint application (tints.py) — the category-vs-exact-name
resolution and the no-cap apply, using a stub RaceTints (no game files)."""

import struct

from furrifier_fo4.tints import apply_tints, RaceTints, TintOption
from furrifier_fo4.customization import ColorRule
from furrifier_fo4.models import Sex


class _Sub:
    def __init__(self, sig, data):
        self.signature = sig
        self.data = bytearray(data)
        self.modified = False


class _Ov:
    """Minimal record stand-in: records add_subrecord calls."""

    def __init__(self):
        self.subs = []

    def add_subrecord(self, sig, data):
        s = _Sub(sig, data)
        self.subs.append(s)
        return s

    def get_subrecord(self, sig):
        for s in self.subs:
            if s.signature == sig:
                return s
        return None

    def teti_indices(self):
        out = []
        for s in self.subs:
            if s.signature == 'TETI':
                _dt, idx = struct.unpack('<HH', s.data[:4])
                out.append(idx)
        return out


def _tints(by_name):
    """A RaceTints with `by_name` ({name_lower: [TintOption]}) for Fox/MALE."""
    rt = RaceTints.__new__(RaceTints)
    rt._clfm_rgb = {100: (255, 0, 0)}
    rt._clfm_edid = {}
    rt._by_race = {'Fox': {Sex.MALE: by_name}}
    return rt


def _opt(index, name, alpha=0.8):
    o = TintOption(index, name)
    o.colors = [(100, alpha, 0)]
    return o


_CATS = {'skin tone': ['Skin tone'], 'mask': ['Face Mask *']}


def _fox():
    return _tints({
        'face mask 1': [_opt(1, 'Face Mask 1')],
        'face mask 2': [_opt(2, 'Face Mask 2')],
        'face mask 3': [_opt(3, 'Face Mask 3')],
        'skin tone': [_opt(9, 'Skin tone', alpha=0.0)],
    })


def test_category_applies_exactly_one_matching_layer():
    rt = _fox()
    scheme = {'Mask': ColorRule(probability=1.0)}      # category -> one of 3
    ov = _Ov()
    apply_tints(None, ov, 'Fox', Sex.MALE, 'sig1', rt,
                color_scheme=scheme, categories=_CATS)
    idx = ov.teti_indices()
    assert 9 in idx                                    # skin tone always
    assert len([i for i in idx if i in (1, 2, 3)]) == 1  # exactly one mask


def test_category_pick_varies_by_signature():
    rt = _fox()
    scheme = {'Mask': ColorRule(probability=1.0)}
    picks = set()
    for sig in ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'):
        ov = _Ov()
        apply_tints(None, ov, 'Fox', Sex.MALE, sig, rt,
                    color_scheme=scheme, categories=_CATS)
        picks |= {i for i in ov.teti_indices() if i in (1, 2, 3)}
    assert len(picks) > 1                              # not always the same mask


def test_exact_layer_name_applies_just_that_layer():
    rt = _fox()
    scheme = {'Face Mask 2': ColorRule(probability=1.0)}   # exact, not a category
    ov = _Ov()
    apply_tints(None, ov, 'Fox', Sex.MALE, 'sigX', rt,
                color_scheme=scheme, categories=_CATS)
    assert [i for i in ov.teti_indices() if i in (1, 2, 3)] == [2]


def test_probability_zero_skips_category():
    rt = _fox()
    scheme = {'Mask': ColorRule(probability=0.0)}
    for sig in ('a', 'b', 'c', 'd'):
        ov = _Ov()
        apply_tints(None, ov, 'Fox', Sex.MALE, sig, rt,
                    color_scheme=scheme, categories=_CATS)
        assert not [i for i in ov.teti_indices() if i in (1, 2, 3)]
