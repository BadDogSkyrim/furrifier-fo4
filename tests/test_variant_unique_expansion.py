"""A Unique NPC must never be variant-expanded.

DeaconAtGoodneighbor (001A0422) inherits its traits from CompanionDeacon
(00045AC9) — a UNIQUE NPC (ACBS Unique flag, 0x20). Variant expansion diversified
CompanionDeacon into a leveled list of furry faces, which is wrong for a unique
character (and it handed one variant an excluded "Hornet's Nest" hair). Unique
NPCs must be furrified in place, never expanded.

Reproduces on the real base+DLC+FurryFallout load order the shipped patch was
built from.

@pytest.mark.gamefiles: needs FO4 + DLCs + FurryFallout.esp installed.
"""

from __future__ import annotations

import os

import pytest

# Exact master list of the shipped FO4FurryPatch.esp (base + DLC + FurryFallout).
PLUGINS = ["Fallout4.esm", "DLCRobot.esm", "DLCCoast.esm",
           "DLCworkshop03.esm", "DLCNukaWorld.esm", "FurryFallout.esp"]


@pytest.fixture(scope="module")
def patch(tmp_path_factory):
    """Furrify the base+DLC load order (no facegen) and load the patch back."""
    from esplib import find_game_data
    try:
        data = str(find_game_data("fo4"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"FO4 game files not available: {exc}")
    for p in PLUGINS:
        if not os.path.exists(os.path.join(data, p)):
            pytest.skip(f"required plugin not installed: {p}")

    from furrifier_fo4 import session
    out = tmp_path_factory.mktemp("furrypatch")
    session.run("ffo_scheme", plugins=PLUGINS, data_dir=data,
                output_dir=str(out), bake_facegen=False, variant_expansion=True)

    # Hardlink the masters beside the fresh patch so from_plugin resolves the
    # NEW patch (not the shipped one in the game folder) against them.
    for p in PLUGINS:
        link = out / p
        if not link.exists():
            try:
                os.link(os.path.join(data, p), link)
            except OSError:
                import shutil
                shutil.copy2(os.path.join(data, p), link)

    from esplib.plugin_set import PluginSet
    return PluginSet.from_plugin(str(out / "FO4FurryPatch.esp"),
                                 data_dir=str(out), game_id="fo4")


@pytest.mark.gamefiles
def test_unique_npc_not_variant_expanded(patch):
    p = patch.get_plugin("FO4FurryPatch.esp")
    lvln_edids = {r.editor_id or "" for r in p.get_records_by_signature("LVLN")}
    npc_edids = {r.editor_id or "" for r in p.get_records_by_signature("NPC_")}

    # Sanity: variant expansion ran on *something* (raiders/guards etc.), so this
    # test is actually exercising the expansion path — not vacuously passing.
    assert any(e.endswith("_FurryVariants") for e in lvln_edids), (
        "no variant expansion happened at all — the test isn't exercising the "
        "path it's meant to guard")

    # CompanionDeacon (00045AC9) is Unique — it must NOT be diversified into a
    # leveled list of variant faces. `expand_at_node` names the list
    # <node>_FurryVariants and the variants <node>_F00.. .
    assert "CompanionDeacon_FurryVariants" not in lvln_edids, (
        "CompanionDeacon (Unique NPC) was variant-expanded into a leveled list")
    offenders = sorted(e for e in npc_edids if e.startswith("CompanionDeacon_F"))
    assert not offenders, (
        "CompanionDeacon (Unique NPC) has minted variant records: " + ", ".join(offenders))
