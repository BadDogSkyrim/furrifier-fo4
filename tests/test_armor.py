"""Tests for the ghoul-armor ARMA race-fix (_arma_lists_race)."""

import importlib.util
import struct
from pathlib import Path

from esplib import Plugin, Record, FormID, LoadOrder, PluginSet

import furrifier_fo4.armor as _armor_mod
from furrifier_fo4.armor import _arma_lists_race, add_race_to_all_armor


def _esplib_plugin_builders():
    """Borrow esplib's synthetic-plugin byte builders (sibling repo)."""
    cf = (Path(_armor_mod.__file__).resolve().parents[3]
          / "esplib" / "tests" / "conftest.py")
    spec = importlib.util.spec_from_file_location("_esplib_conftest", cf)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_simple_plugin, mod.make_subrecord


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


def test_add_race_to_all_armor_keeps_same_objid_armas(tmp_path):
    """Regression: two ARMAs from different plugins that share the low 24 bits
    must both be considered — the winning-override map keyed by bare object
    index dropped one (same class of bug as world.build_winning)."""
    make_simple_plugin, make_subrecord = _esplib_plugin_builders()

    def edid(name):
        return make_subrecord("EDID", name.encode("cp1252") + b"\x00")

    def rnam(fid):
        return make_subrecord("RNAM", struct.pack("<I", fid))

    # Base defines the source + target RACE records.
    base = make_simple_plugin(records=[
        ("RACE", 0x00000FED, edid("SrcRace")),
        ("RACE", 0x00000FEE, edid("TgtRace"))])
    # Two mods, each a NEW ARMA at object index 0x000800 (so identical low 24
    # bits, distinct normalized FormIDs), both listing SrcRace via RNAM.
    mod_a = make_simple_plugin(
        records=[("ARMA", 0x01000800, edid("ArmaA") + rnam(0x00000FED))],
        masters=["Base.esp"])
    mod_b = make_simple_plugin(
        records=[("ARMA", 0x01000800, edid("ArmaB") + rnam(0x00000FED))],
        masters=["Base.esp"])
    (tmp_path / "Base.esp").write_bytes(base)
    (tmp_path / "ModA.esp").write_bytes(mod_a)
    (tmp_path / "ModB.esp").write_bytes(mod_b)

    lo = LoadOrder.from_list(["Base.esp", "ModA.esp", "ModB.esp"],
                             data_dir=str(tmp_path))
    ps = PluginSet(lo)
    ps.load_all()
    races = {r.editor_id: r
             for r in ps.get_plugin("Base.esp").get_records_by_signature("RACE")}

    patch = Plugin.new_plugin(str(tmp_path / "Patch.esp"), masters=[], game="fo4")
    patch.plugin_set = ps
    patched = add_race_to_all_armor(patch, ps, races["SrcRace"], races["TgtRace"])

    assert patched == 2
