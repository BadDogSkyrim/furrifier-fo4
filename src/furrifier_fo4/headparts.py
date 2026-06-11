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
from .util import hash_string, wildcard_match

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

    def __init__(self, plugin_set, exclude=()):
        self.plugin_set = plugin_set
        # EditorID patterns never picked from any pool — a scheme-level
        # exclusion (e.g. rad/magazine hair). Each is a wildcard_match pattern
        # ('*' allowed at start and/or end; a bare string = exact). Filtered at
        # pick time, NOT at build time, so pools still describe each race.
        self.exclude = tuple(exclude)
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

        # normalized HDPT FormID -> (editor_id, type_name), so we can read back
        # an NPC's currently-assigned headpart of a given type (for preserve).
        self._hp_by_fid: dict[int, tuple] = {}
        seen = set()
        for plugin in self.plugin_set:
            for hp in plugin.get_records_by_signature('HDPT'):
                fid = hp.normalize_form_id(hp.form_id).value
                self._hp_by_fid.setdefault(
                    fid, (hp.editor_id or '', self._hp_type(hp)))
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


    def _is_excluded(self, editor_id: str) -> bool:
        return any(wildcard_match(p, editor_id) for p in self.exclude)


    def current_headpart_edid(self, record, type_name: str) -> Optional[str]:
        """EditorID of `record`'s currently-assigned headpart of `type_name`
        (the first matching PNAM), or None. Read before furrification clears the
        NPC's PNAMs, so the hair pick can preserve the NPC's own style."""
        for pnam in record.get_subrecords('PNAM'):
            if pnam.size < 4:
                continue
            fid = record.normalize_form_id(pnam.get_form_id()).value
            info = self._hp_by_fid.get(fid)
            if info is not None and info[1] == type_name:
                return info[0]
        return None


    @staticmethod
    def _preferred(candidates: list, preserve_edid: str,
                   variant_prefix: Optional[str]) -> list:
        """Candidates that preserve the NPC's identity: its own headpart if it's
        valid for the race (tier 1), else `<prefix><edid>` furry variants of it
        (tier 2). A variant matches `prefix+edid` exactly or with a `_`-delimited
        suffix, so vanilla `HairMale1` can't grab `FFO_HairMale10_*`."""
        pe = preserve_edid.lower()
        own = [c for c in candidates if (c.editor_id or '').lower() == pe]
        if own:
            return own
        if not variant_prefix:
            return []
        stem = (variant_prefix + preserve_edid).lower()
        return [c for c in candidates
                if (c.editor_id or '').lower() == stem
                or (c.editor_id or '').lower().startswith(stem + '_')]


    def pick(self, race_edid: str, sex: Sex, type_name: str,
             signature: str, seed: int, whitelist=(),
             preserve_edid: Optional[str] = None,
             variant_prefix: Optional[str] = None) -> Optional[Record]:
        """Deterministically pick one headpart of `type_name` for an NPC.

        Precedence:
          1. `whitelist` (EditorIDs), when non-empty, restricts the pool to
             those headparts (e.g. forcing deer to a specific antler set). It
             wins outright — it may even name a globally-excluded part, honored.
          2. `preserve_edid` (the NPC's own headpart of this type): if it's
             valid for the race, keep it; else a `<variant_prefix><preserve_edid>`
             furry variant of it (e.g. FFO's per-race hair). This **bypasses
             `exclude`** — an NPC whose vanilla hair was rad-damaged keeps the
             matching furry rad hair, while ordinary NPCs never get it at random.
          3. Otherwise a random pick over the pool, with scheme-level
             `self.exclude` (wildcard EditorID patterns) removed.

        Returns None if the resulting pool is empty (caller leaves that slot to
        the race default).
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
                # Whitelist is authoritative — exclusion does not apply.
                idx = hash_string(signature, seed, len(filtered))
                return filtered[idx]
            log.warning("headpart whitelist %r for %s %s matched nothing "
                        "in pool; using full pool", whitelist, race_edid,
                        type_name)
        if preserve_edid:
            preferred = self._preferred(candidates, preserve_edid,
                                        variant_prefix)
            if preferred:
                idx = hash_string(signature, seed, len(preferred))
                return preferred[idx]
        if self.exclude:
            candidates = [c for c in candidates
                          if not self._is_excluded(c.editor_id or '')]
            if not candidates:
                return None
        idx = hash_string(signature, seed, len(candidates))
        return candidates[idx]
