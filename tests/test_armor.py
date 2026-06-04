"""Tests for the ghoul-armor ARMA race-fix (_arma_lists_race)."""

import struct

from esplib import Plugin, Record, FormID

from furrifier_fo4.armor import _arma_lists_race


def _make_patch():
    # Bare plugin so records have a plugin context for normalize_form_id.
    p = Plugin.new_plugin('test.esp', masters=[], game='fo4')
    return p


def _arma(patch, rnam_fid=None, modl_fids=()):
    rec = Record('ARMA', FormID(0x100), 0)
    rec.plugin = patch
    if rnam_fid is not None:
        rec.add_subrecord('RNAM', struct.pack('<I', rnam_fid))
    for f in modl_fids:
        rec.add_subrecord('MODL', struct.pack('<I', f))
    return rec


def test_lists_via_primary_rnam():
    p = _make_patch()
    arma = _arma(p, rnam_fid=0x123)
    assert _arma_lists_race(arma, 0x123)
    assert not _arma_lists_race(arma, 0x999)


def test_lists_via_additional_modl():
    p = _make_patch()
    arma = _arma(p, rnam_fid=0x1, modl_fids=[0x55, 0x66])
    assert _arma_lists_race(arma, 0x66)
    assert _arma_lists_race(arma, 0x1)   # primary still counts
    assert not _arma_lists_race(arma, 0x77)


def test_no_race_subrecords():
    p = _make_patch()
    arma = _arma(p)
    assert not _arma_lists_race(arma, 0x1)
