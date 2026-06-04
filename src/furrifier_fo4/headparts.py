"""Build per-race headpart pools from HDPT records, and pick from them.

Mirrors FFO's CollectRaceHeadparts/LoadHeadpart: each HDPT names a PNAM type,
a DATA sex flag, and an RNAM -> FLST of valid races. A headpart joins a race's
pool (for the matching sex + type) when that race's FormID is in the FLST.

Selection is deterministic: hash the NPC's appearance *signature* (so family
members, which keep distinct signatures, get distinct headparts — relatives,
not clones — while alias members, sharing a signature, match).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from esplib import Record, flst_forms

from .models import Sex
from .util import hash_string

log = logging.getLogger(__name__)

# HDPT PNAM type enum -> our internal headpart-type name. We pick a useful
# subset; the race record supplies defaults for the rest.
HEADPART_TYPES = ('Face', 'Eyes', 'Hair', 'Facial Hair', 'Scar',
                  'Eyebrows', 'Meatcaps', 'Teeth', 'Head Rear', 'Misc')

# HDPT DATA flag bits (FO4): bit1 Male, bit2 Female.
_HDPT_MALE = 0x02
_HDPT_FEMALE = 0x04


class HeadpartPools:
    """race EDID -> sex -> type name -> [HDPT record], built from the load order."""

    def __init__(self, plugin_set):
        self.plugin_set = plugin_set
        # pools[(race_edid, sex)][type_name] -> list[Record]
        self._pools: dict = defaultdict(lambda: defaultdict(list))
        self._build()


    def _build(self) -> None:
        # Index FLST records by normalized FormID for valid-race lookup.
        flst_by_fid: dict[int, Record] = {}
        race_edid_by_fid: dict[int, str] = {}
        for plugin in self.plugin_set:
            for r in plugin.get_records_by_signature('FLST'):
                flst_by_fid[r.normalize_form_id(r.form_id).value] = r
            for r in plugin.get_records_by_signature('RACE'):
                if r.editor_id:
                    race_edid_by_fid[r.normalize_form_id(r.form_id).value] = \
                        r.editor_id

        seen = set()
        for plugin in self.plugin_set:
            for hp in plugin.get_records_by_signature('HDPT'):
                edid = hp.editor_id
                if not edid or edid in seen:
                    continue
                seen.add(edid)
                self._add_headpart(hp, flst_by_fid, race_edid_by_fid)


    def _add_headpart(self, hp: Record, flst_by_fid, race_edid_by_fid) -> None:
        type_name = self._hp_type(hp)
        if type_name is None:
            return
        sexes = self._hp_sexes(hp)
        if not sexes:
            return
        rnam = hp.get_subrecord('RNAM')
        if rnam is None or rnam.size < 4:
            return
        flst_fid = hp.normalize_form_id(rnam.get_form_id()).value
        flst = flst_by_fid.get(flst_fid)
        if flst is None:
            return
        for race_form in flst_forms(flst):
            # flst_forms returns FormIDs in the FLST's own master-list space;
            # normalize each to load-order space to match race_edid_by_fid.
            race_fid = flst.normalize_form_id(race_form.value).value
            race_edid = race_edid_by_fid.get(race_fid)
            if not race_edid:
                continue
            for sex in sexes:
                self._pools[(race_edid, sex)][type_name].append(hp)


    @staticmethod
    def _hp_type(hp: Record) -> Optional[str]:
        pnam = hp.get_subrecord('PNAM')
        if pnam is None or pnam.size < 4:
            return None
        import struct
        idx = struct.unpack('<I', pnam.data[:4])[0]
        names = ('Misc', 'Face', 'Eyes', 'Hair', 'Facial Hair', 'Scar',
                 'Eyebrows', 'Meatcaps', 'Teeth', 'Head Rear')
        return names[idx] if idx < len(names) else None


    @staticmethod
    def _hp_sexes(hp: Record) -> tuple:
        data = hp.get_subrecord('DATA')
        if data is None or data.size < 1:
            return (Sex.MALE, Sex.FEMALE)  # unflagged = both
        flags = data.data[0]
        male = bool(flags & _HDPT_MALE)
        female = bool(flags & _HDPT_FEMALE)
        if not male and not female:
            return (Sex.MALE, Sex.FEMALE)
        out = []
        if male:
            out.append(Sex.MALE)
        if female:
            out.append(Sex.FEMALE)
        return tuple(out)


    def pool(self, race_edid: str, sex: Sex, type_name: str) -> list:
        return self._pools.get((race_edid, sex), {}).get(type_name, [])


    def pick(self, race_edid: str, sex: Sex, type_name: str,
             signature: str, seed: int, whitelist=()) -> Optional[Record]:
        """Deterministically pick one headpart of `type_name` for an NPC.

        `whitelist` (EditorIDs), when non-empty, restricts the candidate pool
        to those headparts — e.g. race_customization forcing deer to use only
        a specific antler set. Returns None if the (filtered) pool is empty
        (caller leaves that slot to the race's defaults).
        """
        candidates = self.pool(race_edid, sex, type_name)
        if not candidates:
            return None
        # Sort by EditorID for a stable order independent of load sequence.
        candidates = sorted(candidates, key=lambda r: r.editor_id or '')
        if whitelist:
            wl = {w.lower() for w in whitelist}
            filtered = [c for c in candidates
                        if (c.editor_id or '').lower() in wl]
            if filtered:
                candidates = filtered
            else:
                log.warning("headpart whitelist %r for %s %s matched nothing "
                            "in pool; using full pool", whitelist, race_edid,
                            type_name)
        idx = hash_string(signature, seed, len(candidates))
        return candidates[idx]
