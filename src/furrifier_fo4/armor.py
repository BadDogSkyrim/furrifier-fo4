"""Armor-addon (ARMA) race fix for furrified races with a non-human body.

Most FFO furry races reuse the vanilla human body, so vanilla armor renders on
them with no edit. Ghouls are the exception: a ghoul furrified to (say)
Snekdog has a different body, so any armor addon that lists GhoulRace must also
list the furry race or the piece won't render on the furrified ghoul.

Ports FFO's AddRaceToAllArmor (BDFurryArmorFixup): walk every winning ARMA;
if it lists the source race (primary RNAM or in the Additional Races MODL
array), add the target race to its Additional Races.
"""

from __future__ import annotations

import logging

from esplib import Plugin, Record

log = logging.getLogger(__name__)


def _arma_lists_race(arma: Record, race_fid: int) -> bool:
    """True if the ARMA's primary race (RNAM) or any Additional Race (MODL)
    is `race_fid` (load-order-normalized)."""
    rnam = arma.get_subrecord('RNAM')
    if rnam is not None and rnam.size >= 4:
        if arma.normalize_form_id(rnam.get_form_id()).value == race_fid:
            return True
    for sr in arma.get_subrecords('MODL'):
        if sr.size >= 4 and arma.normalize_form_id(sr.get_form_id()).value == race_fid:
            return True
    return False


def add_race_to_all_armor(patch: Plugin, plugin_set,
                          source_race: Record, target_race: Record) -> int:
    """For every winning ARMA that lists `source_race`, add `target_race` to
    its Additional Races. Creates/updates the ARMA override in `patch`.
    Returns the number of ARMAs patched.

    Winning-override only: walk each record's last definition across the load
    order so we don't patch a record a later plugin already replaced. Keyed by
    normalized FormID (not bare object index) so ARMAs from different plugins
    that share the low 24 bits don't shadow each other.
    """
    source_fid = source_race.normalize_form_id(source_race.form_id).value
    target_fid = target_race.normalize_form_id(target_race.form_id)

    winning: dict[int, Record] = {}
    for plugin in plugin_set:
        if plugin is patch:
            continue
        for arma in plugin.get_records_by_signature('ARMA'):
            winning[arma.normalize_form_id(arma.form_id).value] = arma

    patched = 0
    for arma in winning.values():
        if not _arma_lists_race(arma, source_fid):
            continue
        if _arma_lists_race(arma, target_fid.value):
            continue  # already has it
        ov = patch.copy_record(arma, arma.plugin)
        patch.add_recursive_masters(target_race.plugin)
        # Append a MODL (Additional Race) entry pointing at the target race.
        modl = ov.add_subrecord('MODL', b'\x00\x00\x00\x00')
        patch.write_form_id(modl, 0, target_fid)
        ov.modified = True
        patched += 1

    log.info("ghoul-armor: added %s to %d ARMAs listing %s",
             target_race.editor_id, patched, source_race.editor_id)
    return patched
