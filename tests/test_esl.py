"""ESL (light/ESPFE) gating — pure, no game files.

A run can emit the patch as a light plugin, but only when its NEW records fit
the ESL object-ID range (0x800..0xFFF = 2048). esplib mints new-record object
IDs from 0x800 upward, so the count is simply next_object_id - 0x800. Overrides
keep their master's FormID and don't count.
"""

from esplib import Plugin

from furrifier_fo4.session import (
    ESL_MAX_NEW_RECORDS, apply_esl_flag, esl_new_record_count,
)


def _new_patch(tmp_path):
    return Plugin.new_plugin(str(tmp_path / "Patch.esp"), masters=[], game="fo4")


def test_limit_is_2048():
    assert ESL_MAX_NEW_RECORDS == 2048


def test_new_record_count_tracks_minted_records(tmp_path):
    patch = _new_patch(tmp_path)
    assert esl_new_record_count(patch) == 0
    patch.new_record("GLOB", edid="FurryGlob1")
    patch.new_record("GLOB", edid="FurryGlob2")
    assert esl_new_record_count(patch) == 2


def test_empty_patch_fits_esl(tmp_path):
    patch = _new_patch(tmp_path)
    made_light, count = apply_esl_flag(patch)
    assert made_light is True
    assert count == 0
    assert patch.is_esl is True


def test_at_limit_fits(tmp_path):
    patch = _new_patch(tmp_path)
    patch.header.next_object_id = 0x800 + ESL_MAX_NEW_RECORDS  # 0x1000
    made_light, count = apply_esl_flag(patch)
    assert count == ESL_MAX_NEW_RECORDS
    assert made_light is True
    assert patch.is_esl is True


def test_over_limit_stays_esp(tmp_path):
    patch = _new_patch(tmp_path)
    patch.header.next_object_id = 0x800 + ESL_MAX_NEW_RECORDS + 1
    made_light, count = apply_esl_flag(patch)
    assert count == ESL_MAX_NEW_RECORDS + 1
    assert made_light is False
    assert patch.is_esl is False
