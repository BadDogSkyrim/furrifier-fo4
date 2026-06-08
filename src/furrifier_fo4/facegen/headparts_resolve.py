"""Resolve an NPC's effective head-part set for facegen assembly.

Mirrors how the CK builds a facegen nif's shape list (ported from the Skyrim
furrifier, FO4-adapted):

  1. Start from the RACE's default head parts for the NPC's sex.
  2. Each NPC PNAM head part overrides the race default of the same type (or
     adds a type the race doesn't define — Scar, Facial Hair).
  3. Transitively pull in HNAM "Extra Parts" (hair -> hairline, etc.).

Each resolved part yields {hdpt_edid, hdpt_type, source_nif, textures}. No tri
/ morph refs: furrify clears the NPC's morphs, so the head uses race-default
geometry and nothing is baked into the verts.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

log = logging.getLogger(__name__)

HDPT_FACE = 1  # PNAM type code for the main head (gets FaceCustomization).

# FO4 TXST slot -> PyNifly shader texture slot (for HDPT TNAM overrides, e.g.
# eye-colour variants that share one nif).
_TXST_SLOTS = {"TX00": "Diffuse", "TX01": "Normal", "TX07": "Specular"}


def _hdpt_type(hdpt) -> Optional[int]:
    pnam = hdpt.get_subrecord("PNAM")
    if pnam is None or pnam.size < 4:
        return None
    return struct.unpack("<I", pnam.data[:4])[0]


def _resolve_refs(srs, plugin_set, source) -> list:
    out = []
    for sr in srs:
        try:
            t = plugin_set.resolve_form_id(sr.get_form_id(), source.plugin)
        except Exception:
            t = None
        if t is not None:
            out.append(t)
    return out


def _race_default_headparts(race, is_female: bool, plugin_set) -> list:
    """HDPT records from the RACE's Head Data section for the sex (NAM0 #1 =
    male, #2 = female; HEAD subrecords point at the HDPTs)."""
    target = 2 if is_female else 1
    n = 0
    in_section = False
    heads = []
    for sr in race.subrecords:
        if sr.signature == "NAM0":
            n += 1
            in_section = (n == target)
        elif in_section and sr.signature == "HEAD":
            heads.append(sr)
    return _resolve_refs(heads, plugin_set, race)


def _expand_hnam_extras(seeds: list, plugin_set) -> list:
    """Seeds plus every HDPT transitively reachable via HNAM extra parts."""
    seen: set = set()
    result: list = []
    queue: list = []

    def push(hp):
        key = hp.editor_id or id(hp)
        if key in seen:
            return
        seen.add(key)
        result.append(hp)
        queue.append(hp)

    for s in seeds:
        push(s)
    while queue:
        cur = queue.pop(0)
        hnams = [sr for sr in cur.subrecords if sr.signature == "HNAM"]
        # HNAM is an array of HDPT FormIDs.
        for sr in hnams:
            n = sr.size // 4
            for i in range(n):
                try:
                    t = plugin_set.resolve_form_id(sr.get_form_id(i), cur.plugin)
                except Exception:
                    t = None
                if t is not None:
                    push(t)
    return result


def _texture_overrides(hdpt, plugin_set) -> dict:
    """HDPT TNAM -> TXST slot textures (textures-relative, Data-relative out)."""
    tnam = hdpt.get_subrecord("TNAM")
    if tnam is None:
        return {}
    try:
        txst = plugin_set.resolve_form_id(tnam.get_form_id(), hdpt.plugin)
    except Exception:
        txst = None
    if txst is None:
        return {}
    out = {}
    for sr in txst.subrecords:
        slot = _TXST_SLOTS.get(sr.signature)
        if slot is None:
            continue
        path = sr.data.rstrip(b"\x00").decode("cp1252", "replace")
        if path:
            out[slot] = path if path.lower().startswith("textures") \
                else "textures\\" + path.replace("/", "\\")
    return out


def _chargen_tri(hdpt) -> Optional[str]:
    """The HDPT's Chargen Morph tri (Parts entry NAM0 type 2 -> NAM1 filename),
    meshes-relative, or None. This .tri holds the chargen shape-key (MPPM)
    morphs the face-morph bake applies to the head verts."""
    ptype = None
    for sr in hdpt.subrecords:
        if sr.signature == "NAM0" and len(sr.data) >= 4:
            ptype = int.from_bytes(sr.data[:4], "little")
        elif sr.signature == "NAM1" and ptype == 2:
            rel = sr.data.rstrip(b"\x00").decode("cp1252", "replace")
            if rel:
                return "meshes\\" + rel.replace("/", "\\")
    return None


def _entry(hdpt, plugin_set) -> Optional[dict]:
    modl = hdpt.get_subrecord("MODL")
    if modl is None:
        return None
    rel = modl.data.rstrip(b"\x00").decode("cp1252", "replace")
    if not rel:
        return None
    src = "meshes\\" + rel.replace("/", "\\")
    return {
        "hdpt_edid": hdpt.editor_id,
        "hdpt_type": _hdpt_type(hdpt),
        "source_nif": src,
        # The sibling facebones-skinned head, for baking region (FMRI/FMRS)
        # morphs (Face head only).
        "facebones_nif": (src[:-4] + "_faceBones.nif"
                          if src.lower().endswith(".nif") else None),
        "textures": _texture_overrides(hdpt, plugin_set),
        "chargen_tri": _chargen_tri(hdpt),   # only the Face head uses this
    }


def resolve_headparts(npc, race, plugin_set, is_female: bool) -> list:
    """Effective head-part entries for `npc` (race defaults, PNAM overrides by
    type, HNAM extras)."""
    by_type: dict = {}
    type_none: list = []
    if race is not None:
        for d in _race_default_headparts(race, is_female, plugin_set):
            t = _hdpt_type(d)
            (type_none.append(d) if t is None else by_type.__setitem__(t, d))

    pnam_srs = [sr for sr in npc.subrecords if sr.signature == "PNAM"]
    for hp in _resolve_refs(pnam_srs, plugin_set, npc):
        t = _hdpt_type(hp)
        (type_none.append(hp) if t is None else by_type.__setitem__(t, hp))

    seeds = list(by_type.values()) + type_none
    out = []
    for hp in _expand_hnam_extras(seeds, plugin_set):
        e = _entry(hp, plugin_set)
        if e is not None:
            out.append(e)
    return out
