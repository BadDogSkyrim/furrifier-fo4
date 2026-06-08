"""Apply face morphs (head shaping) to a furrified NPC from the race catalog.

Two independent FO4 systems, written as separate NPC subrecords:

  - **Regions** (bone transforms) -> `FMRI` (region index) + `FMRS` (7 floats:
    Position X/Y/Z, Rotation X/Y/Z, Scale, all sliders -1..1; + 8 trailing zero
    bytes). The region index is read verbatim from the RACE's per-sex Face
    Morphs (FMRN name -> FMRI index).
  - **Morph-group presets** (named chargen shape keys) -> paired `MSDK` (key =
    the preset's `MPPI`) / `MSDV` (weight, a -1..1 slider). The preset is
    resolved from the RACE's per-sex Morph Groups (MPGN group -> MPPN preset ->
    MPPI). A preset can be named directly under its group (explicit form) or
    under a region, in which case the region's `AssociatedMorphGroup` (from the
    FacialBoneRegions JSON) names the group.

Phase 1 writes the records only. The bake (applying the deformation to the head
geometry) comes later; until then the records drive the engine's runtime head.
See PLAN_FO4_FACEMORPHS.md.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from esplib import Record

from .models import Sex

log = logging.getLogger(__name__)

_FMRS_TRAILING = b"\x00" * 8   # 8 zero bytes follow the 7 floats (real-data exact)


# ---------------------------------------------------------------------------
# Parsed TOML spec
# ---------------------------------------------------------------------------

@dataclass
class RegionMorph:
    """One region entry: an optional bone transform + presets named under it.
    `sex` (None=both / 'male' / 'female') scopes it to one sex's NPCs — the
    RACE defines regions AND morph-group presets per sex, often with different
    names, so a block is usually sex-specific."""
    name: str
    position: Optional[tuple] = None     # (x, y, z) sliders
    rotation: Optional[tuple] = None     # (x, y, z) sliders
    scale: Optional[float] = None        # single slider, all axes
    presets: list = field(default_factory=list)   # [(preset_name, weight)]
    sex: Optional[str] = None

    def has_transform(self) -> bool:
        return (self.position is not None or self.rotation is not None
                or self.scale is not None)


@dataclass
class GroupMorph:
    """An explicit `<MPGN> = ["<MPPN>", weight]` preset, optionally sex-scoped."""
    group: str
    preset: str
    weight: float
    sex: Optional[str] = None


@dataclass
class FaceMorphSpec:
    regions: list = field(default_factory=list)   # [RegionMorph]
    groups: list = field(default_factory=list)    # [GroupMorph]


def _norm_sex(raw) -> Optional[str]:
    """A block's `sex`: 'male'/'female', or None for both/omitted."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("both", ""):
        return None
    if s in ("male", "female"):
        return s
    log.warning("facemorphs: unknown sex %r (treating as both)", raw)
    return None


def _vec3(val, ctx: str) -> Optional[tuple]:
    if not (isinstance(val, list) and len(val) == 3
            and all(isinstance(c, (int, float)) for c in val)):
        log.warning("facemorphs %s: expected [x, y, z], got %r; ignored", ctx, val)
        return None
    return (float(val[0]), float(val[1]), float(val[2]))


def parse_facemorphs(blocks: list, race_name: str) -> FaceMorphSpec:
    """Parse the `[[facemorphs.<race>]]` block list into a FaceMorphSpec.

    A top-level **table** value is a region (`position`/`rotation`/`scale` build
    the transform; any other key is an `MPPN` preset -> weight via the region's
    group). A top-level **array** value is an explicit `<MPGN> = ["<MPPN>",
    weight]` preset. Unknown shapes warn and are skipped.
    """
    spec = FaceMorphSpec()
    for block in blocks:
        block_sex = _norm_sex(block.get("sex"))
        for key, val in block.items():
            if key == "sex":
                continue
            if isinstance(val, dict):
                region = _parse_region(key, val, race_name)
                region.sex = block_sex
                spec.regions.append(region)
            elif isinstance(val, list):
                grp = _parse_group_entry(key, val, race_name)
                if grp is not None:
                    grp.sex = block_sex
                    spec.groups.append(grp)
            else:
                log.warning("facemorphs %s: %r must be a region table or a "
                            "[preset, weight] array, got %r; ignored",
                            race_name, key, val)
    return spec


def _parse_region(name: str, table: dict, race_name: str) -> RegionMorph:
    region = RegionMorph(name=name)
    for k, v in table.items():
        kl = k.lower()
        if kl == "position":
            region.position = _vec3(v, f"{race_name}/{name}.position")
        elif kl == "rotation":
            region.rotation = _vec3(v, f"{race_name}/{name}.rotation")
        elif kl == "scale":
            if isinstance(v, (int, float)):
                region.scale = float(v)
            else:
                log.warning("facemorphs %s/%s.scale: expected a number, got "
                            "%r; ignored", race_name, name, v)
        elif isinstance(v, (int, float)):
            region.presets.append((k, float(v)))   # MPPN preset -> weight
        else:
            log.warning("facemorphs %s/%s: key %r must be position/rotation/"
                        "scale or a preset weight, got %r; ignored",
                        race_name, name, k, v)
    return region


def _parse_group_entry(group: str, val: list, race_name: str):
    if not (len(val) == 2 and isinstance(val[0], str)
            and isinstance(val[1], (int, float))):
        log.warning("facemorphs %s: %r must be [\"<preset>\", weight], got %r; "
                    "ignored", race_name, group, val)
        return None
    return GroupMorph(group, val[0], float(val[1]))


# ---------------------------------------------------------------------------
# RACE-side index: region names -> FMRI, morph groups -> MPPN -> MPPI
# ---------------------------------------------------------------------------

class RaceMorphs:
    """Per (race EDID, sex): region name -> FMRI index, and morph group ->
    preset name -> MPPI, parsed from RACE records.

    Mirrors RaceTints' raw-subrecord walk: NAM0 splits male/female; MPGN opens a
    morph group whose MPPI/MPPN presets follow; FMRI/FMRN give the regions. All
    names are stored lowercased for case-insensitive catalog lookups.
    """

    def __init__(self, plugin_set):
        self.plugin_set = plugin_set
        self._by_race: dict = {}
        for plugin in plugin_set:
            for race in plugin.get_records_by_signature("RACE"):
                if race.editor_id and race.editor_id not in self._by_race:
                    self._by_race[race.editor_id] = self._parse_race(race)

    def _parse_race(self, race: Record) -> dict:
        plugin = race.plugin
        # sex -> {"fmri": {name: index}, "groups": {group: {preset: mppi}},
        #         "mppm": {mppi: chargen-morph-name}}
        out = {Sex.MALE: {"fmri": {}, "groups": {}, "mppm": {}},
               Sex.FEMALE: {"fmri": {}, "groups": {}, "mppm": {}}}
        section = None
        cur_group: Optional[str] = None
        pending_mppi: Optional[int] = None
        pending_fmri: Optional[int] = None
        for sr in race.subrecords:
            s = sr.signature
            if s == "NAM0":
                section = Sex.MALE if section is None else Sex.FEMALE
                cur_group = pending_mppi = pending_fmri = None
            elif section is None:
                continue
            elif s == "MPGN":
                cur_group = _zstr(sr).lower()
                out[section]["groups"].setdefault(cur_group, {})
                pending_mppi = None
            elif s == "MPPI" and sr.size >= 4:
                pending_mppi = struct.unpack("<I", sr.data[:4])[0]
            elif s == "MPPN":
                name = _lstr(sr, plugin).lower()
                if cur_group is not None and pending_mppi is not None and name:
                    out[section]["groups"][cur_group][name] = pending_mppi
                # keep pending_mppi — its MPPM (chargen morph name) follows
            elif s == "MPPM":
                # The chargen .tri shape-key name (plain string, not localized).
                mppm = _zstr(sr)
                if pending_mppi is not None and mppm:
                    out[section]["mppm"][pending_mppi] = mppm
            elif s == "FMRI" and sr.size >= 4:
                pending_fmri = struct.unpack("<I", sr.data[:4])[0]
            elif s == "FMRN":
                name = _lstr(sr, plugin).lower()
                # First writer wins on a duplicate region name (FFO ships two
                # "Neck" regions; the meaningful regions have unique names).
                if pending_fmri is not None and name:
                    out[section]["fmri"].setdefault(name, pending_fmri)
                pending_fmri = None
        return out

    def fmri_for(self, race_edid: str, sex: Sex,
                 region_name: str) -> Optional[int]:
        r = self._by_race.get(race_edid)
        return r[sex]["fmri"].get(region_name.lower()) if r else None

    def mppi_for(self, race_edid: str, sex: Sex, group: str,
                 preset: str) -> Optional[int]:
        r = self._by_race.get(race_edid)
        if not r:
            return None
        return r[sex]["groups"].get(group.lower(), {}).get(preset.lower())

    def mppm_for(self, race_edid: str, sex: Sex, mppi: int) -> Optional[str]:
        """The chargen .tri shape-key name (MPPM) for a preset key (MPPI) — the
        bake reads an NPC's MSDK keys back out and needs the morph name to look
        up the tri delta."""
        r = self._by_race.get(race_edid)
        return r[sex]["mppm"].get(mppi) if r else None


def _zstr(sr) -> str:
    return sr.data.rstrip(b"\x00").decode("cp1252", "replace").strip()


def _lstr(sr, plugin) -> str:
    """A localized string subrecord: a 4-byte string ID in a localized plugin,
    else an inline zstring."""
    if sr.size == 4 and plugin is not None and plugin.is_localized:
        sid = struct.unpack("<I", sr.data[:4])[0]
        return (plugin.resolve_string(sid) or "").strip()
    return _zstr(sr)


# ---------------------------------------------------------------------------
# FacialBoneRegions JSON: region name -> associated morph group (+ bones, kept
# for the later bake phase)
# ---------------------------------------------------------------------------

_REGIONS_SUBDIR = ("Meshes", "Actors", "Character", "CharacterAssets")


class FacialBoneRegions:
    """Lazily reads `<raceEDID>FacialBoneRegions<Sex>.txt` (JSON) from the data
    dir, exposing each region's `AssociatedMorphGroup` (by name) and its bones
    with their Minima/Maxima transforms (by the region's numeric `ID`, which
    equals the RACE Face Morph `FMRI` index — so a baked NPC's FMRI/FMRS records
    map straight to the bones the facebone bake deforms)."""

    def __init__(self, data_dir):
        self.base = Path(data_dir).joinpath(*_REGIONS_SUBDIR)
        # (race, sex) -> (by_name: {name: region}, by_id: {id: region})
        self._cache: dict = {}

    def _load(self, race_edid: str, sex: Sex):
        key = (race_edid, sex)
        if key in self._cache:
            return self._cache[key]
        suffix = "Male" if sex == Sex.MALE else "Female"
        path = self.base / f"{race_edid}FacialBoneRegions{suffix}.txt"
        by_name: dict = {}
        by_id: dict = {}
        try:
            with open(path, "rb") as f:
                regions = json.load(f)
            for r in regions:
                name = (r.get("Name") or "").lower()
                if name:
                    by_name.setdefault(name, r)   # first wins on dup names
                rid = r.get("ID")
                if rid is not None:
                    by_id.setdefault(int(rid), r)
        except FileNotFoundError:
            log.debug("no FacialBoneRegions for %s %s (%s)", race_edid, suffix,
                      path)
        except (ValueError, OSError) as exc:
            log.warning("FacialBoneRegions %s: %s", path, exc)
        self._cache[key] = (by_name, by_id)
        return self._cache[key]

    def associated_group(self, race_edid: str, sex: Sex,
                         region_name: str) -> Optional[str]:
        region = self._load(race_edid, sex)[0].get(region_name.lower())
        return region.get("AssociatedMorphGroup") if region else None

    def bones_for_fmri(self, race_edid: str, sex: Sex, fmri: int) -> list:
        """[(bone_name, minima, maxima)] for the region whose ID == `fmri`
        (minima/maxima are the JSON Position/Rotation/Scale dicts at slider
        -1/+1). Empty if the region or file is missing. Covers BonesA + BonesB."""
        region = self._load(race_edid, sex)[1].get(int(fmri))
        if region is None:
            return []
        out = []
        for key in ("BonesA", "BonesB"):
            for b in region.get(key) or []:
                name = b.get("Bone")
                if name and b.get("Maxima") and b.get("Minima"):
                    out.append((name, b["Minima"], b["Maxima"]))
        return out


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _pack_fmrs(region: RegionMorph) -> bytes:
    px, py, pz = region.position or (0.0, 0.0, 0.0)
    rx, ry, rz = region.rotation or (0.0, 0.0, 0.0)
    scale = region.scale if region.scale is not None else 0.0
    return struct.pack("<7f", px, py, pz, rx, ry, rz, scale) + _FMRS_TRAILING


def apply_facemorphs(patch, ov: Record, race_edid: str, sex: Sex,
                     spec: Optional[FaceMorphSpec], race_morphs: RaceMorphs,
                     bone_regions: Optional[FacialBoneRegions] = None) -> int:
    """Write a race/breed's face morphs onto a furrified NPC override `ov`:
    FMRI/FMRS region transforms + MSDK/MSDV morph-group presets. Returns the
    number of morph entries written. No-op when `spec` is None.

    The caller has already cleared any inherited FMRI/FMRS/MSDK/MSDV. Subrecords
    are appended in any order; esplib's save-time sort places them per the FO4
    NPC_ schema.
    """
    if spec is None:
        return 0
    msdk: list = []     # u32 preset keys (MPPI)
    msdv: list = []     # -1..1 weights
    written = 0
    sex_token = "female" if sex == Sex.FEMALE else "male"

    def for_this_sex(entry_sex) -> bool:
        return entry_sex is None or entry_sex == sex_token

    def add_preset(group: Optional[str], preset: str, weight: float,
                   ctx: str) -> None:
        if not group:
            log.warning("facemorphs %s: %s -> no morph group; skipped", ctx,
                        preset)
            return
        mppi = race_morphs.mppi_for(race_edid, sex, group, preset)
        if mppi is None:
            log.warning("facemorphs %s: preset %r not in group %r for %s; "
                        "skipped", ctx, preset, group, race_edid)
            return
        msdk.append(mppi)
        msdv.append(max(-1.0, min(1.0, weight)))

    # Regions: bone transform (FMRI/FMRS) + presets named under the region
    # (resolved via the region's AssociatedMorphGroup). Sex-filtered.
    for region in spec.regions:
        if not for_this_sex(region.sex):
            continue
        if region.has_transform():
            fmri = race_morphs.fmri_for(race_edid, sex, region.name)
            if fmri is None:
                log.warning("facemorphs %s: region %r not defined; transform "
                            "skipped", race_edid, region.name)
            else:
                ov.add_subrecord("FMRI", struct.pack("<I", fmri))
                ov.add_subrecord("FMRS", _pack_fmrs(region))
                written += 1
        for preset, weight in region.presets:
            group = (bone_regions.associated_group(race_edid, sex, region.name)
                     if bone_regions is not None else None)
            add_preset(group, preset, weight, f"{race_edid}/{region.name}")

    # Explicit morph-group presets.
    for g in spec.groups:
        if not for_this_sex(g.sex):
            continue
        add_preset(g.group, g.preset, g.weight, f"{race_edid} [{g.group}]")

    if msdk:
        ov.add_subrecord("MSDK", struct.pack(f"<{len(msdk)}I", *msdk))
        ov.add_subrecord("MSDV", struct.pack(f"<{len(msdv)}f", *msdv))
        written += len(msdk)
    return written
