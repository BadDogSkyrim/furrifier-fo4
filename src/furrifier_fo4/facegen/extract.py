"""Extract the data the FO4 face-tint compositor needs.

Two things the record-patch path (`tints.py`) deliberately drops, because
they're only needed for baking, not for writing TETI/TEND:

  - the **mask texture path(s)** (RACE option TTET), and
  - the **blend operation** (option-level TTEB, and per-template-colour TTEC).

`RaceTintTemplates` walks every RACE once and indexes each tint Option by
`(race_edid, sex, TETI index)` -> masks + blend, so a baked NPC's TETI/TEND
layers can be resolved back to the masks and blend ops that produced them.

`npc_tint_layers(npc)` reads a furrified NPC override's applied Face Tint
Layers (the TETI/TEND pairs `tints.py` wrote) and joins them to the race
template, yielding ready-to-composite layer dicts.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from ..models import Sex

log = logging.getLogger(__name__)

# RACE TETI Slot enum index for the skin-tone layer (wbDefinitionsFO4
# tint-template Slot enum). The skin tone seeds the overlay base.
_SLOT_SKIN_TONE = 12


class _Option:
    """One race tint Option's bake data: its masks and blend ops."""

    __slots__ = ('slot', 'masks', 'option_blend', 'blend_by_tmpl')

    def __init__(self, slot: int):
        self.slot = slot
        self.masks: list[str] = []          # Data-relative .dds paths (TTET)
        self.option_blend = 0               # TTEB (option-level)
        self.blend_by_tmpl: dict[int, int] = {}  # TEND template idx -> TTEC blend

    def blend_for(self, tmpl_index: int) -> int:
        """Blend op for a TEND template colour index: the per-colour TTEC op
        if known, else the option-level TTEB, else Default."""
        b = self.blend_by_tmpl.get(tmpl_index)
        return b if b is not None else self.option_blend


class RaceTintTemplates:
    """race EDID -> sex -> {TETI index -> _Option}, parsed from RACE records."""

    def __init__(self, plugin_set):
        self.plugin_set = plugin_set
        self._by_race: dict = {}
        for plugin in plugin_set:
            for race in plugin.get_records_by_signature('RACE'):
                if race.editor_id and race.editor_id not in self._by_race:
                    self._by_race[race.editor_id] = self._parse_race(race)

    def _parse_race(self, race) -> dict:
        by_sex: dict = {Sex.MALE: {}, Sex.FEMALE: {}}
        section: Optional[Sex] = None
        opt: Optional[_Option] = None
        index = 0
        for sr in race.subrecords:
            s = sr.signature
            if s == 'NAM0':
                section = Sex.MALE if section is None else Sex.FEMALE
                opt = None
            elif s == 'TETI' and section is not None and sr.size >= 4:
                _slot, index = struct.unpack('<HH', sr.data[:4])
                opt = _Option(_slot)
                by_sex[section][index] = opt
            elif opt is None or section is None:
                continue
            elif s == 'TTET':
                path = sr.data.rstrip(b'\x00').decode('cp1252', 'replace').strip()
                if path:
                    # TTET paths are textures-relative; the resolver wants
                    # Data-relative.
                    p = path.replace('/', '\\')
                    if not p.lower().startswith('textures\\'):
                        p = 'textures\\' + p
                    opt.masks.append(p)
            elif s == 'TTEB' and sr.size >= 4:
                opt.option_blend = struct.unpack('<I', sr.data[:4])[0]
            elif s == 'TTEC':
                # 14-byte entries: Color(4) Alpha(f4) TemplateIndex(u16) Blend(u32)
                for k in range(sr.size // 14):
                    _fid, _alpha, tmpl, bl = struct.unpack_from(
                        '<IfHI', sr.data, k * 14)
                    opt.blend_by_tmpl[tmpl] = bl
        return by_sex

    def option(self, race_edid: str, sex: Sex, index: int) -> Optional[_Option]:
        return self._by_race.get(race_edid, {}).get(sex, {}).get(index)


def npc_tint_layers(npc, race_edid: str, sex: Sex,
                    templates: RaceTintTemplates) -> list[dict]:
    """Read a furrified NPC override's Face Tint Layers (TETI/TEND pairs) and
    join each to its race tint Option, returning compositor-ready layers:

        {mask, color:[r,g,b], intensity, blend, is_skin_tone}

    Layers whose option/mask can't be resolved are dropped (logged) — a
    missing layer is better than aborting the face.
    """
    out: list[dict] = []
    pending_index: Optional[int] = None
    pending_slot = 0
    for sr in npc.subrecords:
        s = sr.signature
        if s == 'TETI' and sr.size >= 4:
            pending_slot, pending_index = struct.unpack('<HH', sr.data[:4])
        elif s == 'TEND' and pending_index is not None:
            d = bytes(sr.data)
            if len(d) >= 4:
                value, r, g, b = d[0], d[1], d[2], d[3]
                tmpl = struct.unpack_from('<h', d, 5)[0] if len(d) >= 7 else 0
                opt = templates.option(race_edid, sex, pending_index)
                if opt is not None and opt.masks:
                    out.append({
                        'mask': opt.masks[0],
                        'color': [r, g, b],
                        'intensity': value / 100.0,
                        'blend': opt.blend_for(tmpl),
                        'is_skin_tone': opt.slot == _SLOT_SKIN_TONE,
                    })
                else:
                    log.debug("no race option/mask for TETI index %d (%s)",
                              pending_index, race_edid)
            pending_index = None
    return out
