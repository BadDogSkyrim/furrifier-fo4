"""Extract the facts the classifier needs from real FO4 NPC records.

Bridges esplib record access to the pure `NpcFacts` the scheme engine
consumes. Kept thin: build a few lookup indexes once, then turn each NPC into
an NpcFacts. The scheme engine does the rest.
"""

from __future__ import annotations

import struct
from typing import Optional

from esplib import Plugin, PluginSet
from esplib.utils import FormID

from .scheme import NpcFacts


class FactExtractor:
    """Turns FO4 NPC_ records into NpcFacts using load-order-wide indexes.

    Resolution:
      - race    : NPC RNAM FormID -> RACE record EditorID
      - factions: NPC SNAM FormIDs -> FACT record EditorIDs
      - name    : NPC FULL (BA2-backed string tables resolve localized IDs)
    """

    def __init__(self, plugin_set: PluginSet):
        self.plugin_set = plugin_set
        self._race_by_fid: dict[int, str] = {}
        self._fact_by_fid: dict[int, str] = {}
        self._build_indexes()


    def _build_indexes(self) -> None:
        """Index RACE and FACT records by normalized (load-order) FormID."""
        for plugin in self.plugin_set:
            for rec in plugin.get_records_by_signature('RACE'):
                if rec.editor_id:
                    fid = rec.normalize_form_id(rec.form_id).value
                    self._race_by_fid[fid] = rec.editor_id
            for rec in plugin.get_records_by_signature('FACT'):
                if rec.editor_id:
                    fid = rec.normalize_form_id(rec.form_id).value
                    self._fact_by_fid[fid] = rec.editor_id


    def race_of(self, npc) -> Optional[str]:
        """EditorID of the NPC's race, or None if unresolved."""
        rnam = npc.get_subrecord('RNAM')
        if rnam is None or rnam.size < 4:
            return None
        fid = npc.normalize_form_id(rnam.get_form_id()).value
        return self._race_by_fid.get(fid)


    def factions_of(self, npc) -> frozenset:
        """EditorIDs of the factions the NPC belongs to.

        FO4 NPC factions are SNAM subrecords: a 4-byte FACT FormID + 1-byte
        rank. There can be several. Unresolved FormIDs are skipped.
        """
        out = set()
        for sr in npc.get_subrecords('SNAM'):
            if sr.size < 4:
                continue
            fid = npc.normalize_form_id(sr.get_form_id(0)).value
            edid = self._fact_by_fid.get(fid)
            if edid:
                out.add(edid)
        return frozenset(out)


    @staticmethod
    def is_female(npc) -> bool:
        """True if the NPC's ACBS Female flag (bit 0) is set."""
        acbs = npc.get_subrecord('ACBS')
        if acbs is None or acbs.size < 4:
            return False
        import struct
        flags = struct.unpack('<I', acbs.data[:4])[0]
        return bool(flags & 0x01)


    def facts_for(self, npc, signature: Optional[str] = None) -> NpcFacts:
        """Build NpcFacts for one NPC. `signature` overrides the hashing
        signature (used when an alias collapses several records)."""
        edid = npc.editor_id or ''
        return NpcFacts(
            signature=signature if signature is not None else edid,
            editor_id=edid,
            race=self.race_of(npc) or '',
            name=npc.full_name,
            factions=self.factions_of(npc),
        )
