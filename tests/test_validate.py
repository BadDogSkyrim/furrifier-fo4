"""Catalog validation (validate.py) — warns on facemorph/tint names a race
doesn't offer. Pure: fed lightweight fakes standing in for RaceMorphs/RaceTints
so the checks + message format are exercised deterministically."""

from furrifier_fo4.validate import validate_customization
from furrifier_fo4.customization import Customization, ColorRule
from furrifier_fo4.facemorphs import parse_facemorphs
from furrifier_fo4.models import Sex


class FakeMorphs:
    """groups: {(race, sex): {group_lower: [preset_lower]}};
       regions: {(race, sex): [region_lower]}."""

    def __init__(self, groups=None, regions=None):
        self._g = groups or {}
        self._r = regions or {}

    def groups_for(self, race, sex):
        return self._g.get((race, sex), {})

    def regions_for(self, race, sex):
        return self._r.get((race, sex), [])

    def mppi_for(self, race, sex, group, preset):
        presets = self._g.get((race, sex), {}).get(group.lower())
        if presets and preset.lower() in [p.lower() for p in presets]:
            return 1
        return None

    def fmri_for(self, race, sex, region):
        if region.lower() in [r.lower() for r in self.regions_for(race, sex)]:
            return 1
        return None


class _Opt:
    def __init__(self, colors):
        self.index = 0
        self.colors = colors        # [(clfm_fid, alpha, tmpl)]


class FakeTints:
    """by_name: {(race, sex): {layer_lower: [_Opt]}}; edids: {fid: edid}."""

    def __init__(self, by_name=None, edids=None):
        self._n = by_name or {}
        self._e = edids or {}

    def options_by_name(self, race, sex):
        return self._n.get((race, sex), {})

    def clfm_edid(self, fid):
        return self._e.get(fid)


def _lion_cust():
    c = Customization()
    c.set_breed('CougarBreed', 'FFOLionRace', 1.0)
    c.facemorphs['Cougar'] = parse_facemorphs(
        [{'Face': ['CougarType1', 1.0]}], 'Cougar')   # array = group form
    c.facemorph_refs['CougarBreed'] = 'Cougar'
    return c


def test_facemorph_preset_wrong_for_sex():
    c = _lion_cust()
    morphs = FakeMorphs(groups={
        ('FFOLionRace', Sex.MALE): {'face': ['cougartype1']},
        ('FFOLionRace', Sex.FEMALE): {'face': ['cougar head', 'default']},
    })
    warns = validate_customization(c, morphs, FakeTints())
    # male resolves; only the female is flagged, with the right suggestion
    assert len(warns) == 1
    w = warns[0]
    assert "female preset 'CougarType1' not in group 'Face'" in w
    assert "FFOLionRace" in w and "did you mean 'cougar head'?" in w


def test_facemorph_all_good_no_warnings():
    c = _lion_cust()
    morphs = FakeMorphs(groups={
        ('FFOLionRace', Sex.MALE): {'face': ['cougartype1']},
        ('FFOLionRace', Sex.FEMALE): {'face': ['cougartype1']},
    })
    assert validate_customization(c, morphs, FakeTints()) == []


def test_tint_color_not_offered():
    c = Customization()
    c.set_breed('FennecBreed', 'FFOFoxRace', 1.0)
    c.colors['FennecBreed'] = 'Fennec'
    c.color_schemes['Fennec'] = {
        None: {'Skin tone': ColorRule(colors=[('ffofurtan', 0.9)])}}
    c.tint_categories['FFOFoxRace'] = {'skin tone': ['skin tone']}
    opt = _Opt([(100, 1.0, 0)])
    tints = FakeTints(
        by_name={('FFOFoxRace', Sex.MALE): {'skin tone': [opt]},
                 ('FFOFoxRace', Sex.FEMALE): {'skin tone': [opt]}},
        edids={100: 'ffofurorange'})
    warns = validate_customization(c, FakeMorphs(), tints)
    # the 'both' block validates against each sex -> one warning per sex
    assert len(warns) == 2
    assert all("doesn't offer color 'ffofurtan'" in w
               and "did you mean 'ffofurorange'?" in w for w in warns)


def test_tint_all_good_no_warnings():
    c = Customization()
    c.set_breed('FennecBreed', 'FFOFoxRace', 1.0)
    c.colors['FennecBreed'] = 'Fennec'
    c.color_schemes['Fennec'] = {
        None: {'Skin tone': ColorRule(colors=[('ffofurorange', 0.9)])}}
    c.tint_categories['FFOFoxRace'] = {'skin tone': ['skin tone']}
    opt = _Opt([(100, 1.0, 0)])
    tints = FakeTints(
        by_name={('FFOFoxRace', Sex.MALE): {'skin tone': [opt]},
                 ('FFOFoxRace', Sex.FEMALE): {'skin tone': [opt]}},
        edids={100: 'ffofurorange'})
    assert validate_customization(c, FakeMorphs(), tints) == []
