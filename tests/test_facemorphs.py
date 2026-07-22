"""Unit tests for face-morph parsing, RACE indexing, and record writing
(facemorphs.py). Pure — synthetic records, no game files."""

import struct

import pytest

from furrifier_fo4.facemorphs import (
    parse_facemorphs, RaceMorphs, apply_facemorphs, FaceMorphSpec, RegionMorph,
    GroupMorph)
from furrifier_fo4.models import Sex


# ----------------------------------------------------------------- stubs ------

class _Sub:
    def __init__(self, sig, data=b""):
        self.signature = sig
        self.data = bytes(data)

    @property
    def size(self):
        return len(self.data)


class _Plugin:
    is_localized = False

    def __init__(self, races):
        self._races = races

    def get_records_by_signature(self, sig):
        return self._races if sig == "RACE" else []

    def __iter__(self):       # a plugin_set is iterable over plugins
        return iter([self])


class _Race:
    def __init__(self, edid, subs, plugin):
        self.editor_id = edid
        self.subrecords = subs
        self.plugin = plugin


class _Ov:
    """Records add_subrecord calls so tests can read back the written bytes."""

    def __init__(self):
        self.subs = []

    def add_subrecord(self, sig, data):
        s = _Sub(sig, data)
        self.subs.append(s)
        return s

    def get(self, sig):
        return [s for s in self.subs if s.signature == sig]

    def get_subrecord(self, sig):
        return next((s for s in self.subs if s.signature == sig), None)


def _u32(n):
    return struct.pack("<I", n)


def _z(s):
    return s.encode("cp1252") + b"\x00"


def _fox_race():
    plugin = _Plugin([])
    subs = [
        _Sub("NAM0"),                                    # -- male --
        _Sub("MPGN", _z("Nose")),
        _Sub("MPPI", _u32(0x36EF36B7)), _Sub("MPPN", _z("Large Tip")),
        _Sub("MPPM", _z("NoseSizeType1")),
        _Sub("MPPI", _u32(0x36EF36C6)), _Sub("MPPN", _z("Small Tip")),
        _Sub("MPPM", _z("NoseSizeType2")),
        _Sub("MPGN", _z("Neck")),
        _Sub("MPPI", _u32(0x36EF36B5)), _Sub("MPPN", _z("Thick Neck")),
        _Sub("MPPM", _z("NeckThickType1")),
        _Sub("FMRI", _u32(9)), _Sub("FMRN", _z("Nose - Full")),
        _Sub("FMRI", _u32(5)), _Sub("FMRN", _z("Ears - Full")),
        _Sub("FMRI", _u32(1)), _Sub("FMRN", _z("Neck")),
        _Sub("FMRI", _u32(0x36EF34BE)), _Sub("FMRN", _z("Neck")),   # dup name
        _Sub("NAM0"),                                    # -- female --
        _Sub("MPGN", _z("Eyes")),
        _Sub("MPPI", _u32(0x36EF36BB)), _Sub("MPPN", _z("Type 1")),
        _Sub("FMRI", _u32(0x36EF34BF)), _Sub("FMRN", _z("Eyes")),
    ]
    race = _Race("FFOFoxRace", subs, plugin)
    plugin._races = [race]
    return RaceMorphs(plugin)


# ----------------------------------------------------------- TOML parsing -----

def test_parse_region_transform_and_preset():
    spec = parse_facemorphs(
        [{"Eyes": {"Slanted": 1.0, "position": [0, 1, 0],
                   "rotation": [-0.1, 0, 0], "scale": 1}}], "WhiteTail")
    assert len(spec.regions) == 1 and not spec.groups
    r = spec.regions[0]
    assert r.name == "Eyes"
    assert r.position == (0.0, 1.0, 0.0)
    assert r.rotation == (-0.1, 0.0, 0.0)
    assert r.scale == 1.0
    assert r.presets == [("Slanted", 1.0)]
    assert r.has_transform()


def test_parse_explicit_group_array():
    spec = parse_facemorphs([{"Brow Type": ["Bushy", 0.75]}], "WhiteTail")
    assert len(spec.groups) == 1 and not spec.regions
    g = spec.groups[0]
    assert (g.group, g.preset, g.weight, g.sex) == ("Brow Type", "Bushy", 0.75, None)


def test_parse_block_sex_tags_entries():
    spec = parse_facemorphs(
        [{"sex": "male", "Nose - Full": {"position": [0, 1, 0]},
          "Nose": ["Large Tip", 0.8]},
         {"sex": "female", "Nose": ["Small Nose Tip", 0.8]}], "Fox")
    # the 'sex' key itself is consumed, not treated as a region/group
    assert [r.name for r in spec.regions] == ["Nose - Full"]
    assert spec.regions[0].sex == "male"
    assert [(g.preset, g.sex) for g in spec.groups] == [
        ("Large Tip", "male"), ("Small Nose Tip", "female")]


def test_parse_preset_only_region_has_no_transform():
    spec = parse_facemorphs([{"Ears - Full": {"Thick Neck": 0.5}}], "Fox")
    r = spec.regions[0]
    assert not r.has_transform()
    assert r.presets == [("Thick Neck", 0.5)]


def test_parse_malformed_group_skipped():
    spec = parse_facemorphs([{"X": ["only-one-elem"]}], "Fox")
    assert spec.groups == []


def test_parse_malformed_scale_ignored():
    spec = parse_facemorphs([{"R": {"scale": "big"}}], "Fox")
    assert spec.regions[0].scale is None


# ------------------------------------------------------------ RACE index ------

def test_race_fmri_lookup_case_insensitive_first_wins():
    rm = _fox_race()
    assert rm.fmri_for("FFOFoxRace", Sex.MALE, "Nose - Full") == 9
    assert rm.fmri_for("FFOFoxRace", Sex.MALE, "nose - full") == 9
    # duplicate "Neck" -> first writer (index 1), not the later hash index
    assert rm.fmri_for("FFOFoxRace", Sex.MALE, "Neck") == 1
    assert rm.fmri_for("FFOFoxRace", Sex.FEMALE, "Eyes") == 0x36EF34BF


def test_race_preset_lookup_per_sex_and_group():
    rm = _fox_race()
    assert rm.mppi_for("FFOFoxRace", Sex.MALE, "Nose", "Large Tip") == 0x36EF36B7
    assert rm.mppi_for("FFOFoxRace", Sex.MALE, "neck", "thick neck") == 0x36EF36B5
    assert rm.mppi_for("FFOFoxRace", Sex.FEMALE, "Eyes", "Type 1") == 0x36EF36BB
    # male has no Eyes presets; unknown preset/group -> None
    assert rm.mppi_for("FFOFoxRace", Sex.MALE, "Eyes", "Type 1") is None
    assert rm.mppi_for("FFOFoxRace", Sex.MALE, "Nose", "Huge") is None


# ------------------------------------------------------------- apply ----------

def test_apply_region_transform_writes_fmri_fmrs():
    rm = _fox_race()
    spec = FaceMorphSpec(regions=[
        RegionMorph("Nose - Full", position=(0.0, 1.0, 0.0), scale=0.5)])
    ov = _Ov()
    n = apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    assert n == 1
    assert struct.unpack("<I", ov.get("FMRI")[0].data)[0] == 9
    fmrs = ov.get("FMRS")[0].data
    assert len(fmrs) == 36                       # 7 floats + 8 trailing zeros
    px, py, pz, rx, ry, rz, sc = struct.unpack_from("<7f", fmrs)
    assert (px, py, pz) == (0.0, 1.0, 0.0)
    assert (rx, ry, rz) == (0.0, 0.0, 0.0)       # omitted rotation -> 0
    assert abs(sc - 0.5) < 1e-6
    assert fmrs[28:] == b"\x00" * 8


def test_apply_explicit_group_writes_msdk_msdv():
    rm = _fox_race()
    spec = FaceMorphSpec(groups=[GroupMorph("Nose", "Large Tip", 0.5),
                                 GroupMorph("Neck", "Thick Neck", -0.8)])
    ov = _Ov()
    apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    keys = struct.unpack("<2I", ov.get("MSDK")[0].data)
    vals = struct.unpack("<2f", ov.get("MSDV")[0].data)
    assert keys == (0x36EF36B7, 0x36EF36B5)
    assert abs(vals[0] - 0.5) < 1e-6 and abs(vals[1] + 0.8) < 1e-6


def test_apply_region_preset_resolves_via_race_record():
    rm = _fox_race()
    # A region whose key matches a morph group ("Neck") resolves its presets
    # straight from the RACE record — no external region->group asset needed.
    spec = FaceMorphSpec(regions=[
        RegionMorph("Neck", presets=[("Thick Neck", 0.3)])])
    ov = _Ov()
    apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    assert struct.unpack("<I", ov.get("MSDK")[0].data)[0] == 0x36EF36B5
    assert not ov.get("FMRI")                    # preset-only region: no transform


def test_apply_unknown_region_skips_transform():
    rm = _fox_race()
    spec = FaceMorphSpec(regions=[RegionMorph("Snout", position=(1.0, 0.0, 0.0))])
    ov = _Ov()
    assert apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm) == 0
    assert not ov.get("FMRI")


def test_apply_weight_clamped_to_slider_range():
    rm = _fox_race()
    spec = FaceMorphSpec(groups=[GroupMorph("Nose", "Large Tip", 5.0)])  # OOR
    ov = _Ov()
    apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    assert struct.unpack("<f", ov.get("MSDV")[0].data)[0] == 1.0


def test_apply_sex_filter_skips_other_sex():
    rm = _fox_race()
    # A male-only preset + a female-only one (a different group's preset);
    # baking a MALE NPC must apply only the male entry.
    spec = FaceMorphSpec(groups=[
        GroupMorph("Nose", "Large Tip", 0.5, sex="male"),
        GroupMorph("Neck", "Thick Neck", 0.5, sex="female")])
    ov = _Ov()
    apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    keys = struct.unpack("<I", ov.get("MSDK")[0].data)
    assert keys == (0x36EF36B7,)                  # only the male "Large Tip"


# --------------------------------------------------- chargen morph (phase 2) --

def test_race_mppm_lookup():
    rm = _fox_race()
    key = rm.mppi_for("FFOFoxRace", Sex.MALE, "Nose", "Large Tip")
    assert rm.mppm_for("FFOFoxRace", Sex.MALE, key) == "NoseSizeType1"
    assert rm.mppm_for("FFOFoxRace", Sex.MALE, 0x36EF36B5) == "NeckThickType1"
    assert rm.mppm_for("FFOFoxRace", Sex.MALE, 0xDEAD) is None


def test_npc_morphs_reads_msdk_msdv_to_mppm():
    from furrifier_fo4.facegen import _npc_morphs
    rm = _fox_race()
    ov = _Ov()
    spec = FaceMorphSpec(groups=[GroupMorph("Nose", "Large Tip", 0.8),
                                 GroupMorph("Neck", "Thick Neck", -0.5)])
    apply_facemorphs(None, ov, "FFOFoxRace", Sex.MALE, spec, rm)
    morphs = _npc_morphs(ov, "FFOFoxRace", Sex.MALE, rm)
    assert [m[0] for m in morphs] == ["NoseSizeType1", "NeckThickType1"]
    assert morphs[0][1] == pytest.approx(0.8) and morphs[1][1] == pytest.approx(-0.5)
    # no race_morphs -> nothing to resolve
    assert _npc_morphs(ov, "FFOFoxRace", Sex.MALE, None) == []


def test_morphed_verts_applies_weighted_delta():
    from furrifier_fo4.facegen import tri_morph

    class _FakeCM:
        basis = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
        morphs = {"M": [(1.0, 0.0, 0.0), (0.0, 2.0, 0.0), (1.0, 1.0, 1.0)]}
        vert_count = 3                              # deltas: (1,0,0),(0,2,0),(0,0,0)

    tri_morph._cache["fake"] = _FakeCM()
    try:
        base = [(10.0, 10.0, 10.0), (20.0, 20.0, 20.0), (30.0, 30.0, 30.0)]
        out = tri_morph.morphed_verts(base, "fake", [("M", 0.5)])
        assert out == [(10.5, 10.0, 10.0), (20.0, 21.0, 20.0), (30.0, 30.0, 30.0)]
        # vert-count mismatch -> base returned unchanged (no corruption)
        assert tri_morph.morphed_verts([(0.0, 0.0, 0.0)], "fake", [("M", 1)]) \
            == [(0.0, 0.0, 0.0)]
        # empty morphs -> the same base object back
        assert tri_morph.morphed_verts(base, "fake", []) is base
    finally:
        tri_morph._cache.pop("fake", None)


def test_morphed_verts_skips_missing_tri_morph_without_warning(caplog):
    # A race-listed morph absent from the .tri (FFO data incompleteness) is
    # skipped silently at bake time (DEBUG, not WARNING — it would otherwise spam
    # once per NPC); present morphs still apply.
    import logging
    from furrifier_fo4.facegen import tri_morph

    class _FakeCM:
        basis = [(0.0, 0.0, 0.0)]
        morphs = {"Real": [(2.0, 0.0, 0.0)]}
        vert_count = 1

    tri_morph._cache["fake"] = _FakeCM()
    try:
        with caplog.at_level(logging.WARNING, logger=tri_morph.log.name):
            out = tri_morph.morphed_verts(
                [(0.0, 0.0, 0.0)], "fake", [("Missing", 1.0), ("Real", 0.5)])
        assert out == [(1.0, 0.0, 0.0)]          # only "Real" applied
        assert "not in tri" not in caplog.text   # missing one warned nothing
    finally:
        tri_morph._cache.pop("fake", None)
