"""Turn a resolved (NPC, furry-race) into a furrified override in the patch.

This is the first G3 slice: the core *race conversion* that produces a valid,
in-game-loadable furry NPC. It mirrors the essential steps of FFO's
NPC_Clean + NPC_SetRace (BDAssetLoaderFO4 / FFO_Furrifier):

  1. copy the NPC into the patch (override)
  2. clean vanilla appearance: drop FTST (head texture), WNAM (skin),
     Head Parts (PNAM), Face Tinting Layers (TETI/TEND), zero face morphs
     (MSDK/MSDV); set FMIN (facial morph intensity) = 1.0
  3. point RNAM at the furry race (child NPCs -> the race's child_race)
  4. set WNAM <- the furry race's own skin (WNAM)

The furry RACE record supplies default head parts and tint templates via its
presets, so this alone yields a correct furry NPC. Per-NPC headpart and tint
*variation* (hashed on the NPC's appearance signature) layers on in a later
slice; the deterministic-signature contract from the scheme engine is what
that future code will hash on — never the resolved race or a family leader.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from esplib import Plugin, Record
from esplib.record import SubRecord

from .util import hash_string

log = logging.getLogger(__name__)

# NPC appearance subrecords cleared before applying the furry race, so no
# vanilla-human head data lingers. (FFO NPC_Clean.)
_CLEAR_SIGS = ('FTST', 'WNAM', 'PNAM', 'MSDK', 'MSDV',
               'TETI', 'TEND', 'MRSV', 'FMRI', 'FMRS')


class RaceLibrary:
    """Indexes furry RACE records by EditorID and exposes what furrify needs.

    Built once from the loaded plugin set. Resolves a scheme race name (or
    its child variant) to the actual RACE record and the record's own skin
    (WNAM) FormID.
    """

    def __init__(self, plugin_set, child_races: Optional[dict] = None):
        self.plugin_set = plugin_set
        # child_races: race EDID -> child race EDID (from race catalog)
        self.child_races = child_races or {}
        self._race_by_edid: dict[str, Record] = {}
        for plugin in plugin_set:
            for rec in plugin.get_records_by_signature('RACE'):
                if rec.editor_id:
                    # Last writer wins = load-order-winning override.
                    self._race_by_edid[rec.editor_id] = rec


    def get(self, edid: str) -> Optional[Record]:
        return self._race_by_edid.get(edid)


    def resolve(self, race_edid: str, is_child: bool) -> Optional[Record]:
        """The RACE record to assign: the child variant for a child NPC if one
        is defined, else the adult race. Returns None if neither exists — the
        caller skips the NPC (a child furrified to a race with no child variant
        is left un-furrified, per PLAN_FO4_SCHEME)."""
        if is_child:
            child_edid = self.child_races.get(race_edid)
            if not child_edid:
                return None
            return self._race_by_edid.get(child_edid)
        return self._race_by_edid.get(race_edid)


def is_child_npc(extractor, npc) -> bool:
    """True if the NPC's vanilla race is a child race."""
    return (extractor.race_of(npc) or '').endswith('ChildRace')


# Headpart types assigned per-NPC, in render order. Hair last so it sits
# after face/eyes in the record (cosmetic; matches FFO's ordering intent).
# Each: (pool category, hash seed, race_customization KEY for prob/whitelist).
# Eyes/Hair have no customization key (always attempted, prob 1.0).
#
# NOTE: the head itself (PNAM type 1 'Face') is deliberately NOT picked from a
# pool. The head shape is a property of the RACE, so it comes from the race's
# defined default head (resolve_headparts reads the race's NAM0 Head Data).
# Picking it from the pool searched every HDPT valid for the race, which on a
# messed-up race def (e.g. FFOTigerRace's FLST also lists the child head
# FFOTigChildHead) could land a child head on an adult — the head ends up
# smaller than the beard/ruff parts, so they float forward of the chin
# (caught on CompanionX6-88). Children get the child head via the child RACE's
# own default, not via a pool pick here.
_HEADPART_PICKS = (
    ('Eyes', 4339, 'EYES'),
    ('Eyebrows', 4421, 'EYEBROWS'),
    ('Facial Hair', 4523, 'FACIAL_HAIR'),
    ('Scar', 4639, 'SCAR'),
    ('Hair', 4751, 'HAIR'),
)


def furrify_npc(patch: Plugin, npc: Record, furry_race: Record,
                race_edid: str = None, sex=None, signature: str = None,
                headpart_pools=None, race_tints=None,
                customization=None) -> Record:
    """Create a furrified override of `npc` in `patch`, assigned to
    `furry_race`. Returns the new override record.

    Assumes `furry_race` is the already-resolved RACE record (adult or child
    variant). The NPC is copied with esplib's copy_record (which remaps
    FormIDs and pulls in masters), then cleaned and re-pointed.

    When `headpart_pools`, `race_edid`, `sex`, and `signature` are supplied,
    per-NPC head parts are picked from the race's HDPT pools and written as
    PNAM subrecords — hashed on `signature`, so family members (distinct
    signatures) get distinct looks while alias members (shared signature)
    match. Without them, the NPC uses the race's default head data.
    """
    ov = patch.copy_record(npc, npc.plugin)
    apply_furry(patch, ov, furry_race, race_edid=race_edid, sex=sex,
                signature=signature, headpart_pools=headpart_pools,
                race_tints=race_tints, customization=customization)
    return ov


def apply_furry(patch: Plugin, ov: Record, furry_race: Record,
                race_edid: str = None, sex=None, signature: str = None,
                headpart_pools=None, race_tints=None,
                customization=None) -> Record:
    """Apply the furry appearance (race + skin + per-NPC headparts/tints/weight)
    to an EXISTING patch record `ov`, in place. `furrify_npc` calls this after
    copying a base; variant-expansion calls it on a freshly-minted variant copy
    (so the same furrification runs without producing another override)."""
    patch.add_recursive_masters(furry_race.plugin)

    # 1. Clean vanilla appearance.
    for sig in _CLEAR_SIGS:
        ov.remove_subrecords(sig)
    _set_fmin(ov, 1.0)

    # 2. Point RNAM at the furry race.
    race_fid = furry_race.normalize_form_id(furry_race.form_id)
    rnam = ov.get_subrecord('RNAM')
    if rnam is None:
        rnam = ov.add_subrecord('RNAM', b'\x00\x00\x00\x00')
    patch.write_form_id(rnam, 0, race_fid)

    # 3. Skin: copy the furry race's WNAM onto the NPC.
    race_wnam = furry_race.get_subrecord('WNAM')
    if race_wnam is not None and race_wnam.size >= 4:
        skin_fid = furry_race.normalize_form_id(race_wnam.get_form_id())
        npc_wnam = ov.add_subrecord('WNAM', b'\x00\x00\x00\x00')
        patch.write_form_id(npc_wnam, 0, skin_fid)

    # 4. Per-NPC head parts from the race's HDPT pools, gated/constrained by
    # race_customization (probability + optional whitelist).
    # NB: test `sex is not None`, not `sex` — Sex.MALE == 0 is falsy.
    if (headpart_pools is not None and race_edid
            and sex is not None and signature):
        for type_name, seed, hp_key in _HEADPART_PICKS:
            rule = (customization.headpart_rule(race_edid, sex, hp_key)
                    if customization is not None else None)
            if rule is not None and rule.probability < 1.0:
                if hash_string(signature, seed + 1, 100) >= rule.probability * 100:
                    continue
            whitelist = rule.whitelist if rule is not None else ()
            hp = headpart_pools.pick(race_edid, sex, type_name, signature,
                                     seed, whitelist=whitelist)
            if hp is None:
                continue
            hp_fid = hp.normalize_form_id(hp.form_id)
            pnam = ov.add_subrecord('PNAM', b'\x00\x00\x00\x00')
            patch.write_form_id(pnam, 0, hp_fid)

    # 5. Per-NPC face tints from the race's tint templates (TETI/TEND + QNAM),
    # optionally constrained by the race's named color scheme.
    if (race_tints is not None and race_edid
            and sex is not None and signature):
        from .tints import apply_tints
        scheme = (customization.color_scheme_for(race_edid)
                  if customization is not None else None)
        cats = (customization.categories_for(race_edid)
                if customization is not None else None)
        apply_tints(patch, ov, race_edid, sex, signature, race_tints,
                    color_scheme=scheme, categories=cats)

    # 6. Weight: remap MWGT thin/musc/fat into the race's configured ranges,
    # hashed on signature so NPCs vary within the band.
    if (customization is not None and race_edid and sex is not None
            and signature):
        ranges = customization.weight_range(race_edid, sex)
        if ranges is not None:
            _apply_weight(ov, ranges, signature)

    ov.modified = True
    return ov


def _apply_weight(ov: Record, ranges, signature: str) -> None:
    """Set MWGT thin/musc/fat each to a deterministic value in its (lo,hi)
    range. `ranges` is [(lo,hi)*3] in 0-1 space."""
    vals = []
    for axis, (lo, hi) in enumerate(ranges):
        if hi <= lo:
            vals.append(lo)
        else:
            # 0..1000 resolution, decorrelated per axis.
            frac = hash_string(signature, 5501 + axis * 137, 1001) / 1000.0
            vals.append(lo + frac * (hi - lo))
    data = struct.pack('<fff', *vals)
    mwgt = ov.get_subrecord('MWGT')
    if mwgt is None:
        ov.add_subrecord('MWGT', data)
    else:
        mwgt.data = bytearray(data)
        mwgt.modified = True


def _set_fmin(record: Record, value: float) -> None:
    """Set (or add) FMIN — Facial Morph Intensity — to `value`."""
    fmin = record.get_subrecord('FMIN')
    data = struct.pack('<f', value)
    if fmin is None:
        record.add_subrecord('FMIN', data)
    else:
        fmin.data = bytearray(data)
        fmin.modified = True
