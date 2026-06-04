"""Build per-race face-tint templates (keyed by FFO category) and apply them.

A furry RACE record carries Male/Female "Tint Layers": groups of Options. Each
Option = a TETI (slot+index) + a localized TTGP *name* + a TTEC array of color
presets (CLFM FormID, alpha, template index).

The raw FO4 TETI *slot* enum is NOT a reliable category — FFO authors park
options in arbitrary slots (a fox "Nose Stripe" can sit in the "Lip Color"
slot). FFO instead categorizes each option by its TTGP **name** via a
translation table (BDAssetLoaderFO4 SetTintLayerTranslations), then applies a
curated set of categories per NPC (FFO_Furrifier FurrifyNPC) — deliberately
NOT applying chargen-only categories like Lip Color, Paint, Scar, Old.

We mirror that: name -> category, group by category, apply the allowlist.
Selection (which option, which color) is hashed on the NPC's appearance
signature so family members differ.
"""

from __future__ import annotations

import logging
import struct
from collections import defaultdict
from typing import Optional

from esplib import Record
from .models import Sex
from .util import hash_string

log = logging.getLogger(__name__)

SKIN_TONE = 'Skin Tone'

# Categories FFO's FurrifyNPC applies, besides Skin Tone (always applied first,
# seeds QNAM). Order here is apply order; the per-NPC cap limits how many land.
# Excluded on purpose (chargen/manual only): Lip Color, Paint, Scar, Old.
# Matches FurrifyNPC's list: Mask, Muzzle, Muzzle Stripe, Chin, Ear, Eyebrow,
# Forehead, Eyeliner, Eyesocket Lower/Upper, Neck, Cheek Color(Lower), Nose.
_APPLY_CATEGORIES = (
    'Mask', 'Muzzle', 'Muzzle Stripe', 'Chin', 'Ear', 'Eyebrow',
    'Forehead', 'Eyeliner', 'Eyesocket Lower', 'Eyesocket Upper',
    'Neck', 'Cheek Color Lower', 'Cheek Color', 'Nose',
)
_MAX_EXTRA_TINTS = 4

# TTGP option name -> FFO category. Ported verbatim from FFO
# SetTintLayerTranslations (BDAssetLoaderFO4). Names not here are ignored
# (e.g. FaceRegions groups, chargen-only oddities).
_NAME_TO_CATEGORY = {
    'Blaze Narrow': 'Forehead', 'Blaze Wide': 'Forehead', 'Cap': 'Forehead',
    'Cheek Color Lower': 'Cheek Color Lower', 'Cheek Color': 'Cheek Color',
    'Cheeks': 'Cheek Color', 'Chin': 'Chin',
    'Cougar 01': 'Muzzle Stripe', 'Cougar 02': 'Muzzle Stripe',
    'Cougar White': 'Muzzle', 'Ears': 'Ear',
    'Eye Lower': 'Eyesocket Lower', 'Eye Shadow': 'Eyeliner',
    'Eye Socket Upper': 'Eyesocket Upper', 'Eye Socket': 'Eyeliner',
    'Eye Stripe': 'Mask', 'Eye Tear': 'Mask', 'Eye Upper': 'Eyesocket Upper',
    'Eyebrow Spot': 'Eyebrow', 'Eyebrow': 'Eyebrow', 'Eyeliner': 'Eyeliner',
    'Face Mask 1': 'Mask', 'Face Mask 2': 'Mask', 'Face Mask 3': 'Mask',
    'Face Mask 4': 'Mask', 'Face Plate': 'Mask', 'Fishbones': 'Paint',
    'Forehead': 'Forehead', 'Gazelle': 'Mask', 'Head Scales': 'Forehead',
    'Lips': 'Lip Color', 'Lower Jaw': 'Chin', 'Mask': 'Mask',
    'Mouche': 'Muzzle Stripe', 'Muzzle Side': 'Muzzle Stripe',
    'Muzzle Small': 'Muzzle', 'Muzzle Stripe': 'Muzzle Stripe',
    'Muzzle Upper': 'Muzzle Stripe', 'Muzzle': 'Muzzle',
    'Nose Color': 'Nose', 'Nose Stripe 1': 'Muzzle Stripe',
    'Nose Stripe 2': 'Muzzle Stripe', 'Nose Stripe': 'Muzzle Stripe',
    'Nose': 'Nose', 'Old': 'Old',
    'Scar - Left Long': 'Scar', 'Skin tone': 'Skin Tone', 'Skull': 'Paint',
    'Star': 'Forehead', 'Stripes 01': 'Mask', 'Stripes 02': 'Mask',
    'Stripes 03': 'Mask', 'Upper Head': 'Forehead', 'White Face': 'Mask',
    'White Face 01': 'Cheek Color', 'White Face 02': 'Cheek Color',
    'White Face 03': 'Cheek Color',
}

_TTEC_ENTRY = 14  # FormID(4) + alpha(f4) + template idx(u16) + blend(u32)


class TintOption:
    """One race tint Option: its TETI index, FFO category, and color presets."""

    __slots__ = ('index', 'category', 'colors')

    def __init__(self, index: int, category: str):
        self.index = index
        self.category = category
        self.colors: list = []  # (clfm_fid, alpha, template_index)


class RaceTints:
    """race EDID -> sex -> category -> [TintOption], parsed from RACE records,
    plus a CLFM FormID -> (r,g,b) lookup."""

    def __init__(self, plugin_set):
        self.plugin_set = plugin_set
        self._by_race: dict = {}
        self._clfm_rgb: dict[int, tuple] = {}
        self._index_clfm()
        for plugin in plugin_set:
            for race in plugin.get_records_by_signature('RACE'):
                if race.editor_id and race.editor_id not in self._by_race:
                    self._by_race[race.editor_id] = self._parse_race(race)


    def _index_clfm(self) -> None:
        self._clfm_edid: dict[int, str] = {}  # fid -> editor id (lower)
        for plugin in self.plugin_set:
            for c in plugin.get_records_by_signature('CLFM'):
                cnam = c.get_subrecord('CNAM')
                if cnam is None or cnam.size < 4:
                    continue
                val = struct.unpack('<I', cnam.data[:4])[0]
                fid = c.normalize_form_id(c.form_id).value
                self._clfm_rgb[fid] = (
                    val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)
                if c.editor_id:
                    self._clfm_edid[fid] = c.editor_id.lower()


    def clfm_edid(self, fid: int) -> Optional[str]:
        return self._clfm_edid.get(fid)


    def _parse_race(self, race: Record) -> dict:
        """Parse a race's Male/Female tint Options, grouped by sex + FFO
        category (resolved from each Option's localized TTGP name).

        Walk: NAM0 markers split male/female; a TETI starts an Option; the
        FIRST TTGP after that TETI is the Option's name; TTEC fills colors.
        """
        plugin = race.plugin
        by_sex: dict = {Sex.MALE: defaultdict(list),
                        Sex.FEMALE: defaultdict(list)}
        section = None
        opt: Optional[TintOption] = None
        slot_index = 0
        awaiting_name = False
        for sr in race.subrecords:
            s = sr.signature
            if s == 'NAM0':
                section = Sex.MALE if section is None else Sex.FEMALE
                opt = None
                awaiting_name = False
            elif s == 'TETI' and section is not None and sr.size >= 4:
                _slot, slot_index = struct.unpack('<HH', sr.data[:4])
                opt = None
                awaiting_name = True
            elif s == 'TTGP' and awaiting_name and section is not None:
                name = self._read_ttgp(sr, plugin)
                awaiting_name = False
                category = _NAME_TO_CATEGORY.get(name)
                if category is not None:
                    opt = TintOption(slot_index, category)
                    by_sex[section][category].append(opt)
                else:
                    opt = None  # uncategorized name: ignore this option
            elif s == 'TTEC' and opt is not None:
                n = sr.size // _TTEC_ENTRY
                for k in range(n):
                    fid, alpha, tmpl = struct.unpack_from(
                        '<IfH', sr.data, k * _TTEC_ENTRY)
                    opt.colors.append(
                        (race.normalize_form_id(fid).value, alpha, tmpl))
        return by_sex


    @staticmethod
    def _read_ttgp(sr, plugin) -> str:
        if sr.size == 4 and plugin is not None and plugin.is_localized:
            sid = struct.unpack('<I', sr.data[:4])[0]
            return (plugin.resolve_string(sid) or '').strip()
        return sr.data.rstrip(b'\x00').decode('cp1252', 'replace').strip()


    def options(self, race_edid: str, sex: Sex, category: str) -> list:
        return self._by_race.get(race_edid, {}).get(sex, {}).get(category, [])


    def rgb(self, clfm_fid: int) -> Optional[tuple]:
        return self._clfm_rgb.get(clfm_fid)


    def pick_layer(self, race_edid: str, sex: Sex, category: str,
                   signature: str, seed: int, color_rule=None):
        """Pick (option, (clfm_fid, alpha, tmpl)) for a category, or None.

        Choose an option, then a color preset with alpha > 0 (skin tone
        allows alpha 0). When `color_rule` (a customization.ColorRule) is
        given with a non-empty palette, restrict to its allowed CLFM EditorIDs
        and override the alpha with the scheme's intensity. Returns None if
        nothing usable.
        """
        opts = self.options(race_edid, sex, category)
        if not opts:
            return None
        option = opts[hash_string(signature, seed, len(opts))]
        allow_zero = (category == SKIN_TONE)

        palette = color_rule.colors if (color_rule and color_rule.colors) else None
        usable = []
        for clfm_fid, alpha, tmpl in option.colors:
            if self.rgb(clfm_fid) is None:
                continue
            if palette is not None:
                edid = self.clfm_edid(clfm_fid)
                match = next((p for p in palette if p[0] == edid), None)
                if match is None:
                    continue
                usable.append((clfm_fid, match[1], tmpl))  # scheme intensity
            elif alpha > 0.0001 or allow_zero:
                usable.append((clfm_fid, alpha, tmpl))
        if not usable:
            return None
        color = usable[hash_string(signature, seed + 7989, len(usable))]
        return option, color


def apply_tints(patch, ov: Record, race_edid: str, sex: Sex,
                signature: str, race_tints: RaceTints,
                color_scheme=None) -> int:
    """Write face-tint layers onto a furrified NPC override. Returns count.

    Skin Tone first (seeds QNAM), then up to _MAX_EXTRA_TINTS categories from
    the curated allowlist, each gated by a deterministic per-category roll.

    `color_scheme` (category -> ColorRule) constrains palette + per-category
    probability when the race has a named scheme; without it, the race's full
    TTEC palette is used at a default ~65% per-category gate.
    """
    def rule_for(cat):
        return color_scheme.get(cat) if color_scheme else None

    written = 0

    skin_rule = rule_for(SKIN_TONE)
    if skin_rule is None or skin_rule.probability >= 1.0 or \
            hash_string(signature, 9001, 100) < skin_rule.probability * 100:
        skin = race_tints.pick_layer(race_edid, sex, SKIN_TONE, signature,
                                     9523, color_rule=skin_rule)
        if skin is not None:
            option, color = skin
            _write_layer(ov, option, color, race_tints)
            _set_qnam(ov, race_tints.rgb(color[0]), color[1])
            written += 1

    extra = 0
    for ci, category in enumerate(_APPLY_CATEGORIES):
        if extra >= _MAX_EXTRA_TINTS:
            break
        rule = rule_for(category)
        # Gate: scheme probability if present, else the default ~65% temper.
        if rule is not None:
            if hash_string(signature, 2189 + ci * 53, 100) >= rule.probability * 100:
                continue
        elif color_scheme is not None:
            # Race has a scheme but doesn't list this category -> skip it
            # (scheme is authoritative about which categories apply).
            continue
        elif hash_string(signature, 2189 + ci * 53, 100) >= 65:
            continue
        picked = race_tints.pick_layer(race_edid, sex, category,
                                       signature, 1783 + ci * 41,
                                       color_rule=rule)
        if picked is None:
            continue
        option, color = picked
        _write_layer(ov, option, color, race_tints)
        written += 1
        extra += 1

    return written


def _write_layer(ov: Record, option: TintOption, color, race_tints):
    """Append a TETI (datatype=1 + index) + TEND (value + RGB + template)."""
    clfm_fid, alpha, tmpl = color
    rgb = race_tints.rgb(clfm_fid) or (255, 255, 255)
    ov.add_subrecord('TETI', struct.pack('<HH', 1, option.index))
    val = max(0, min(255, round(alpha * 100)))
    tmpl_s = tmpl if tmpl < 0x8000 else tmpl - 0x10000
    ov.add_subrecord('TEND',
                     struct.pack('<BBBBBh', val, rgb[0], rgb[1], rgb[2], 0,
                                 tmpl_s))


def _set_qnam(ov: Record, rgb, alpha) -> None:
    if rgb is None:
        return
    r, g, b = rgb
    data = struct.pack('<ffff', r / 255.0, g / 255.0, b / 255.0, float(alpha))
    q = ov.get_subrecord('QNAM')
    if q is None:
        ov.add_subrecord('QNAM', data)
    else:
        q.data = bytearray(data)
        q.modified = True
