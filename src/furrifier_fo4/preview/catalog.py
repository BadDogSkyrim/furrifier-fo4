"""Fast NPC catalog for the preview picker.

Populating the picker doesn't need the full furrification session (~28s of
parsing every record of every plugin). It only needs the list of furry-relevant
NPCs — so this does a *partial* load of just the NPC_ and RACE groups (esplib's
`only_signatures`, ~5s) and enumerates them.

"Furry-relevant" = the NPC's BASE race (its winning override among non-furrifier
plugins) is in FURRIFIABLE_RACES. That naturally includes NPCs an earlier run
already furrified (their base is still Human/Ghoul) and excludes creatures,
robots, turrets, and synths.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from esplib import LoadOrder, PluginSet, find_game_data

from ..extract import FactExtractor
from ..models import FURRIFIABLE_RACES, is_furrifier_plugin

log = logging.getLogger(__name__)


class PreviewCatalog:
    """Partial-load the load order and list furry-relevant NPCs.

    `entries()` returns `[(objid, editor_id), ...]` sorted by EditorID — the
    picker's source list. Depends only on (data_dir, plugins), NOT the scheme,
    so changing the scheme never requires rebuilding it.
    """

    def __init__(self, data_dir: Optional[str] = None,
                 plugins: Optional[list] = None):
        self.data = Path(data_dir or find_game_data("fo4"))
        if plugins is None:
            plugins = list(LoadOrder.from_game("fo4", active_only=True))
        lo = LoadOrder.from_list(plugins, data_dir=str(self.data))
        ps = PluginSet(lo)
        ps.load_all(only_signatures={"NPC_", "RACE"})

        extractor = FactExtractor(ps)
        # Base winning record per object id: the last override that ISN'T
        # furrifier output, so an already-furrified NPC still reports its
        # vanilla race (and its vanilla EditorID) here.
        base_winning: dict = {}
        for plugin in ps:
            if is_furrifier_plugin(plugin):
                continue
            for npc in plugin.get_records_by_signature("NPC_"):
                base_winning[npc.form_id.value & 0xFFFFFF] = npc

        entries = []
        for objid, npc in base_winning.items():
            if npc.editor_id and extractor.race_of(npc) in FURRIFIABLE_RACES:
                entries.append((objid, npc.editor_id))
        entries.sort(key=lambda t: t[1].lower())
        self._entries = entries
        log.info("catalog: %d furry-relevant NPCs", len(entries))

    def entries(self) -> list:
        return list(self._entries)
