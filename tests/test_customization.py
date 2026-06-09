"""Tests for race_customization parsing (customization.py)."""

import tomllib

import pytest

from collections import Counter

from furrifier_fo4.customization import (
    Customization, HeadpartRule, _apply_row, _parse_weight_range,
    _parse_headpart_value, _parse_tint_categories, load_customization)
from furrifier_fo4.models import Sex


def build(toml_text):
    cust = Customization()
    for row in tomllib.loads(toml_text).get('race_customization', []):
        _apply_row(cust, row, 'test')
    return cust


def test_child_race():
    c = build('''
    [[race_customization]]
    race = "FoxRace"
    child_race = "FoxChildRace"
    ''')
    assert c.child_race('FoxRace') == 'FoxChildRace'
    assert c.child_race('Unknown') is None


def test_weight_range_legacy_list_three_axes():
    c = build('''
    [[race_customization]]
    race = "Cheetah"
    weight_range = [[40, 100], [0, 60], [0, 30]]
    ''')
    r = c.weight_range('Cheetah', Sex.MALE)
    # Scheme 0-100 -> MWGT 0-1; stored as {axis_index: (lo, hi)}.
    assert r == {0: (0.4, 1.0), 1: (0.0, 0.6), 2: (0.0, 0.3)}


def test_weight_range_table_subset():
    c = build('''
    [[race_customization]]
    race = "Cheetah"
    weight_range = {thin = [40, 100], fat = [0, 20]}
    ''')
    # Only thin (0) and fat (2) pinned; muscle (1) omitted = the slack axis.
    assert c.weight_range('Cheetah', Sex.MALE) == {0: (0.4, 1.0), 2: (0.0, 0.2)}


def test_weight_range_malformed_ignored():
    c = build('''
    [[race_customization]]
    race = "Bad"
    weight_range = [[40, 100], [0, 60]]
    ''')
    assert c.weight_range('Bad', Sex.MALE) is None


def test_headpart_bare_probability():
    c = build('''
    [[race_customization]]
    race = "Deer"
    EYEBROWS = 1.0
    FACIAL_HAIR = 0.15
    ''')
    assert c.headpart_rule('Deer', Sex.MALE, 'EYEBROWS').probability == 1.0
    fh = c.headpart_rule('Deer', Sex.MALE, 'FACIAL_HAIR')
    assert fh.probability == pytest.approx(0.15)
    assert fh.whitelist == ()


def test_headpart_inline_table_whitelist():
    c = build('''
    [[race_customization]]
    race = "Deer"
    EYEBROWS = {probability = 1.0, headpart = ["Antler1", "Antler2"]}
    ''')
    r = c.headpart_rule('Deer', Sex.MALE, 'EYEBROWS')
    assert r.probability == 1.0
    assert r.whitelist == ('Antler1', 'Antler2')


def test_headpart_default_when_unset():
    c = build('[[race_customization]]\nrace = "X"\n')
    r = c.headpart_rule('X', Sex.MALE, 'SCAR')
    assert r.probability == 1.0 and r.whitelist == ()


def test_sex_specific_then_both_fallback():
    c = build('''
    [[race_customization]]
    race = "Horse"
    sex = "female"
    FACIAL_HAIR = 0.0

    [[race_customization]]
    race = "Horse"
    EYEBROWS = 0.5
    ''')
    # Female-specific row wins for females.
    assert c.headpart_rule('Horse', Sex.FEMALE, 'FACIAL_HAIR').probability == 0.0
    # The both/None row applies to males (no male-specific).
    assert c.headpart_rule('Horse', Sex.MALE, 'EYEBROWS').probability == 0.5


def test_colors_reference():
    c = build('''
    [[race_customization]]
    race = "WhiteTail"
    colors = "WhiteTailScheme"
    ''')
    assert c.colors['WhiteTail'] == 'WhiteTailScheme'


def test_facemorphs_reference():
    c = build('''
    [[race_customization]]
    race = "Fennec"
    facemorphs = "FennecMorphs"
    ''')
    assert c.facemorph_refs['Fennec'] == 'FennecMorphs'
    # resolves the ref to the shared [facemorphs.Name] block
    c.facemorphs['FennecMorphs'] = sentinel = object()
    assert c.facemorphs_for('Fennec') is sentinel
    assert c.facemorphs_for('Unreferenced') is None


def test_breeds_on_row():
    c = build('''
    [[race_customization]]
    race = "FFOFoxRace"
    breeds = [["RedFoxBreed", 0.9], ["FennecBreed", 0.1]]
    ''')
    assert set(c.breeds) == {'RedFoxBreed', 'FennecBreed'}
    assert c.breeds['RedFoxBreed'].parent_race_edid == 'FFOFoxRace'
    assert c.breeds['RedFoxBreed'].probability == 0.9
    assert c.breeds['FennecBreed'].probability == 0.1
    # registered under the parent in row order (drives the breed roll)
    assert [b.name for b in c.breeds_by_parent['FFOFoxRace']] == \
        ['RedFoxBreed', 'FennecBreed']


def test_breeds_on_row_malformed_warns(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        c = build('''
        [[race_customization]]
        race = "FFOFoxRace"
        breeds = [["RedFoxBreed", 0.9], "FennecBreed"]
        ''')
    assert set(c.breeds) == {'RedFoxBreed'}        # good entry still registers
    assert 'malformed breed entry' in caplog.text


def test_color_scheme_per_sex_union(tmp_path):
    # both block applies to either sex; matching-sex block overrides/adds
    (tmp_path / 'r.toml').write_text(
        '[[race_customization]]\nrace = "Bird"\ncolors = "Plume"\n\n'
        '[[color_schemes.Plume]]\nsex = "both"\nEyes = [["Yellow", 0.9]]\n\n'
        '[[color_schemes.Plume]]\nsex = "male"\nBody = [["Blue", 0.9]]\n\n'
        '[[color_schemes.Plume]]\nsex = "female"\nBody = [["Brown", 0.9]]\n')
    c = load_customization(tmp_path)
    male = c.color_scheme_for('Bird', Sex.MALE)
    female = c.color_scheme_for('Bird', Sex.FEMALE)
    assert set(male) == {'Eyes', 'Body'} and set(female) == {'Eyes', 'Body'}
    assert male['Body'].colors == [('blue', 0.9)]      # sex-specific differs
    assert female['Body'].colors == [('brown', 0.9)]
    assert male['Eyes'].colors == [('yellow', 0.9)]    # shared 'both' to either


def test_color_scheme_single_table_is_both(tmp_path):
    # a single [color_schemes.X] table = one implicit 'both' block, both sexes
    (tmp_path / 'r.toml').write_text(
        '[[race_customization]]\nrace = "Fox"\ncolors = "RedFox"\n\n'
        '[color_schemes.RedFox]\nMask = [["White", 0.9]]\n')
    c = load_customization(tmp_path)
    assert 'Mask' in c.color_scheme_for('Fox', Sex.MALE)
    assert 'Mask' in c.color_scheme_for('Fox', Sex.FEMALE)


def test_facemorphs_single_table(tmp_path):
    # a single [facemorphs.X] table works too (one implicit 'both' block)
    (tmp_path / 'r.toml').write_text(
        '[[race_customization]]\nrace = "Fox"\nfacemorphs = "FoxM"\n\n'
        '[facemorphs.FoxM]\nsex = "both"\n"Nose - Full" = { scale = -0.5 }\n')
    c = load_customization(tmp_path)
    spec = c.facemorphs_for('Fox')
    assert spec is not None and spec.regions


def test_parse_tint_categories_de_underscores_and_lowercases():
    cats = _parse_tint_categories({
        'Mask': ['*mask*', 'Face Plate'],
        'Muzzle_Stripe': ['Mouche'],
        'Skin Tone': 'Skin tone',          # bare string -> single-pattern list
    })
    assert cats['mask'] == ['*mask*', 'Face Plate']
    assert cats['muzzle stripe'] == ['Mouche']
    assert cats['skin tone'] == ['Skin tone']


def test_tint_categories_are_file_scoped(tmp_path):
    (tmp_path / 'a.toml').write_text(
        '[tint_categories]\nMask = ["*mask*"]\n'
        '[[race_customization]]\nrace = "RaceA"\n')
    (tmp_path / 'b.toml').write_text(
        '[[race_customization]]\nrace = "RaceB"\n')
    c = load_customization(tmp_path)
    assert c.categories_for('RaceA') == {'mask': ['*mask*']}
    assert c.categories_for('RaceB') == {}   # b.toml has no [tint_categories]


# ---- breeds ----------------------------------------------------------------


def test_breeds_parsed_and_resolved(tmp_path):
    (tmp_path / 'r.toml').write_text(
        '[[race_customization]]\nrace = "DeerRace"\nchild_race = "DeerChildRace"\n'
        'breeds = [["ElkBreed", 0.3], ["MooseBreed", 0.7]]\n'
        '[[race_customization]]\nrace = "ElkBreed"\ncolors = "Elk"\n')
    c = load_customization(tmp_path)
    assert set(c.breeds) == {'ElkBreed', 'MooseBreed'}
    assert c.resolve_race_or_breed('ElkBreed') == ('DeerRace', c.breeds['ElkBreed'])
    assert c.resolve_race_or_breed('DeerRace') == ('DeerRace', None)


def test_roll_breed_deterministic_and_distributes():
    c = Customization()
    c.set_breed('A', 'P', 0.5)
    c.set_breed('B', 'P', 0.5)
    assert c.roll_breed('NPC_123', 'P') is c.roll_breed('NPC_123', 'P')  # stable
    counts = Counter(c.roll_breed(f'npc{i}', 'P').name for i in range(400))
    assert counts['A'] > 100 and counts['B'] > 100   # both appear, ~50/50
    assert c.roll_breed('x', 'NoBreeds') is None      # unknown parent


def test_roll_breed_breedless_remainder():
    c = Customization()
    c.set_breed('A', 'P', 0.3)   # 70% breed-less slice
    none_count = sum(1 for i in range(400) if c.roll_breed(f'n{i}', 'P') is None)
    assert none_count > 150      # roughly 70% land breed-less


def test_set_breed_overflow_dropped():
    c = Customization()
    c.set_breed('A', 'P', 0.8)
    c.set_breed('B', 'P', 0.5)   # 0.8 + 0.5 > 1.0 -> dropped
    assert 'B' not in c.breeds
    assert [b.name for b in c.breeds_by_parent['P']] == ['A']


def test_breed_inherits_parent_then_wildcard():
    c = Customization()
    c.set_breed('ElkBreed', 'DeerRace', 1.0)
    c.headpart_rules[('DeerRace', None, 'EYES')] = HeadpartRule(probability=0.5)
    c.headpart_rules[('ElkBreed', 'male', 'EYEBROWS')] = \
        HeadpartRule(probability=1.0, whitelist=['Antler'])
    # breed-specific rule wins
    assert c.headpart_rule('ElkBreed', Sex.MALE, 'EYEBROWS').whitelist == ('Antler',)
    # breed silent on EYES -> inherits parent
    assert c.headpart_rule('ElkBreed', Sex.MALE, 'EYES').probability == 0.5
    # color scheme falls back breed -> parent, then breed wins when defined
    c.colors['DeerRace'] = 'DeerScheme'
    c.color_schemes['DeerScheme'] = {None: {'x': 1}}
    assert c.color_scheme_for('ElkBreed', Sex.MALE) == {'x': 1}
    c.colors['ElkBreed'] = 'ElkScheme'
    c.color_schemes['ElkScheme'] = {None: {'y': 2}}
    assert c.color_scheme_for('ElkBreed', Sex.MALE) == {'y': 2}
    # facemorphs resolve the same way: breed -> parent, breed wins when set
    c.facemorph_refs['DeerRace'] = 'DeerM'; c.facemorphs['DeerM'] = {'m': 1}
    assert c.facemorphs_for('ElkBreed') == {'m': 1}
    c.facemorph_refs['ElkBreed'] = 'ElkM'; c.facemorphs['ElkM'] = {'m': 2}
    assert c.facemorphs_for('ElkBreed') == {'m': 2}


# ------------------------------------------------ top-level key lint (load) ---

def test_top_level_unknown_key_warns(tmp_path, caplog):
    # A typo'd top-level table (here `race_customizaton`) would otherwise drop
    # the whole section silently — the lint must flag it.
    (tmp_path / 'x.toml').write_text(
        '[[race_customizaton]]\nrace = "FoxRace"\n')
    with caplog.at_level('WARNING'):
        load_customization(tmp_path)
    assert any('unrecognized top-level key' in r.message
               and 'race_customizaton' in r.message for r in caplog.records)


def test_top_level_known_keys_no_warn(tmp_path, caplog):
    (tmp_path / 'x.toml').write_text(
        '[[race_customization]]\n'
        'race = "FoxRace"\n'
        'child_race = "FoxChildRace"\n'
        'breeds = [["B", 0.5]]\n\n'
        '[tint_categories]\n'
        'Mask = ["face mask*"]\n\n'
        '[color_schemes.Foo]\n'
        'Mask = [["probability", 1.0]]\n')
    with caplog.at_level('WARNING'):
        cust = load_customization(tmp_path)
    assert not any('unrecognized top-level key' in r.message
                   for r in caplog.records)
    # ...and the recognized sections still loaded.
    assert cust.child_race('FoxRace') == 'FoxChildRace'
    assert 'B' in cust.breeds


def test_top_level_breeds_now_warns(tmp_path, caplog):
    # breeds moved onto [[race_customization]] rows; a top-level `breeds` array
    # is no longer recognized and must warn rather than silently load nothing.
    (tmp_path / 'x.toml').write_text(
        'breeds = [{breed = "B", race = "FoxRace", probability = 0.5}]\n')
    with caplog.at_level('WARNING'):
        cust = load_customization(tmp_path)
    assert any('unrecognized top-level key' in r.message
               and 'breeds' in r.message for r in caplog.records)
    assert 'B' not in cust.breeds
