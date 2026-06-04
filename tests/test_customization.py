"""Tests for race_customization parsing (customization.py)."""

import tomllib

import pytest

from furrifier_fo4.customization import (
    Customization, _apply_row, _parse_weight_range, _parse_headpart_value)
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


def test_weight_range_three_axes():
    c = build('''
    [[race_customization]]
    race = "Cheetah"
    weight_range = [[40, 100], [0, 60], [0, 30]]
    ''')
    r = c.weight_range('Cheetah', Sex.MALE)
    # Scheme 0-100 -> MWGT 0-1.
    assert r == [(0.4, 1.0), (0.0, 0.6), (0.0, 0.3)]


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
