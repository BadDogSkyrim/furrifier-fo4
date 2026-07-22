"""Story-critical NPCs must never be furrified.

The player (00000007), Shaun's forms, and both player-spouse presets are owned by
the FFO-Player-* plugins, which set the player's race themselves. Furrifying them
would clobber that choice and mangle the intro/family scenes. session.run skips
every FormID in STORY_PROTECTED_FORM_IDS, so none may appear as an NPC_ override
in the patch.

Reproduces on the real base+DLC+FurryFallout load order the shipped patch was
built from.

@pytest.mark.gamefiles: needs FO4 + DLCs + FurryFallout.esp installed.
"""

from __future__ import annotations

import os

import pytest

from furrifier_fo4.session import STORY_PROTECTED_FORM_IDS

# Exact master list of the shipped FO4FurryPatch.esp (base + DLC + FurryFallout).
PLUGINS = ["Fallout4.esm", "DLCRobot.esm", "DLCCoast.esm",
           "DLCworkshop03.esm", "DLCNukaWorld.esm", "FurryFallout.esp"]


@pytest.fixture(scope="module")
def run_result(tmp_path_factory):
    """Furrify the base+DLC load order (no facegen); return (patch, stats)."""
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
    stats = session.run("ffo_scheme", plugins=PLUGINS, data_dir=data,
                        output_dir=str(out), bake_facegen=False,
                        variant_expansion=True)

    for p in PLUGINS:
        link = out / p
        if not link.exists():
            try:
                os.link(os.path.join(data, p), link)
            except OSError:
                import shutil
                shutil.copy2(os.path.join(data, p), link)

    from esplib.plugin_set import PluginSet
    patch = PluginSet.from_plugin(str(out / "FO4FurryPatch.esp"),
                                  data_dir=str(out), game_id="fo4")
    return patch, stats


@pytest.mark.gamefiles
def test_story_npcs_not_furrified(run_result):
    patch, stats = run_result

    # All seven protected records exist in this load order and were skipped.
    assert stats['story_protected'] == len(STORY_PROTECTED_FORM_IDS)

    # None of them may appear as an NPC_ override in the patch.
    p = patch.get_plugin("FO4FurryPatch.esp")
    patched = {r.normalize_form_id(r.form_id).value
               for r in p.get_records_by_signature("NPC_")}
    leaked = sorted(STORY_PROTECTED_FORM_IDS & patched)
    assert not leaked, (
        "story-critical NPCs furrified into the patch: "
        + ", ".join(f"{fid:08X}" for fid in leaked))
