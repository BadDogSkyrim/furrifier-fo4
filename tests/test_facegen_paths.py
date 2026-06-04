"""FaceGen path ownership: textures go under the DEFINING plugin, not the
override. The FO4 engine looks for facegen under the base record's plugin, so
a furrified vanilla NPC's textures must live under Fallout4.esm\\ (or the DLC
esm), even though the patch is the winning override.
"""

from types import SimpleNamespace

from furrifier_fo4.facegen import base_plugin_for


def _npc(file_index):
    return SimpleNamespace(form_id=SimpleNamespace(file_index=file_index))


def _patch(masters, name="FO4FurryTest.esp"):
    return SimpleNamespace(header=SimpleNamespace(masters=masters),
                           file_path=SimpleNamespace(name=name))


def test_vanilla_npc_owned_by_fallout4():
    patch = _patch(["Fallout4.esm", "FurryFallout.esp", "DLCCoast.esm"])
    assert base_plugin_for(_npc(0), patch) == "Fallout4.esm"


def test_dlc_npc_owned_by_dlc():
    patch = _patch(["Fallout4.esm", "FurryFallout.esp", "DLCCoast.esm"])
    assert base_plugin_for(_npc(2), patch) == "DLCCoast.esm"


def test_patch_created_record_owned_by_patch():
    # file_index past the master list -> the record is new in the patch.
    patch = _patch(["Fallout4.esm"], name="FO4FurryTest.esp")
    assert base_plugin_for(_npc(1), patch) == "FO4FurryTest.esp"
