"""Regression test for the preview NPC catalog (preview/catalog.py).

The picker is populated from PreviewCatalog, which independently indexes the
winning base NPCs. Keying that map by bare object index dropped records from
different plugins that share the low 24 bits (e.g. the 3DNPC_FO4.esp NPCs that
collided with FFO test NPCs), so they never appeared in the picker.
"""

import importlib.util
import struct
from pathlib import Path

from esplib import LoadOrder, PluginSet

import furrifier_fo4.preview.catalog as _catalog_mod
from furrifier_fo4.preview.catalog import PreviewCatalog


def _esplib_plugin_builders():
    cf = (Path(_catalog_mod.__file__).resolve().parents[4]
          / "esplib" / "tests" / "conftest.py")
    spec = importlib.util.spec_from_file_location("_esplib_conftest", cf)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_simple_plugin, mod.make_subrecord


def test_catalog_keeps_same_objid_npcs_from_different_plugins(tmp_path):
    make_simple_plugin, make_subrecord = _esplib_plugin_builders()

    def edid(name):
        return make_subrecord("EDID", name.encode("cp1252") + b"\x00")

    def rnam(fid):
        return make_subrecord("RNAM", struct.pack("<I", fid))

    # Base defines HumanRace (furrifiable).
    base = make_simple_plugin(records=[("RACE", 0x00000FED, edid("HumanRace"))])
    # Two mods, each a NEW NPC at object index 0x000800 (identical low 24 bits,
    # distinct normalized FormIDs), both HumanRace.
    mod_a = make_simple_plugin(
        records=[("NPC_", 0x01000800, edid("NpcA") + rnam(0x00000FED))],
        masters=["Base.esp"])
    mod_b = make_simple_plugin(
        records=[("NPC_", 0x01000800, edid("NpcB") + rnam(0x00000FED))],
        masters=["Base.esp"])
    (tmp_path / "Base.esp").write_bytes(base)
    (tmp_path / "ModA.esp").write_bytes(mod_a)
    (tmp_path / "ModB.esp").write_bytes(mod_b)

    cat = PreviewCatalog(data_dir=str(tmp_path),
                         plugins=["Base.esp", "ModA.esp", "ModB.esp"])
    edids = {edid for _key, edid in cat.entries()}
    assert edids == {"NpcA", "NpcB"}, edids
    # Keys must be the load-order-normalized FormIDs (so they match what
    # PreviewSession/world.build_winning use for bake lookups), not bare objids.
    keys = {key for key, _edid in cat.entries()}
    assert keys == {0x01000800, 0x02000800}, [hex(k) for k in keys]
