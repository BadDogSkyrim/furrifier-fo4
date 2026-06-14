"""Build per-race face-tint templates (keyed by layer NAME) and apply them.

A furry RACE record carries Male/Female "Tint Layers": groups of Options. Each
Option = a TETI (slot+index) + a localized TTGP *name* + a TTEC array of color
presets (CLFM FormID, alpha, template index).

We store each race's options indexed by their TTGP **name**. Which names apply,
and how they group into categories, is NOT hardcoded — it comes from the race
catalog's per-file `[tint_categories]` block (customization.py), so it's
transparent and editable. A color-scheme or default key resolves either to a
CATEGORY (de-underscored name found in `[tint_categories]` -> a random one of
its matching layers) or, failing that, to an EXACT layer name (just that
layer). Selection (which option, which color) is hashed on the NPC's appearance
signature so family members differ.
"""

from __future__ import annotations

import logging
import struct
from collections import defaultdict
from typing import Optional

from esplib import Record
from .models import Sex
from .util import hash_string, wildcard_match

log = logging.getLogger(__name__)

# Category key treated specially: always applied first and seeds QNAM. Matched
# case-insensitively against the de-underscored category keys.
SKIN_TONE_KEY = 'skin tone'
# No-scheme per-category gate (percent). No cap — a race applies all the
# categories its file defines (sans Skin Tone) at this odds.
_DEFAULT_GATE = 65

_TTEC_ENTRY = 14  # FormID(4) + alpha(f4) + template idx(u16) + blend(u32)


class TintOption:
    """One race tint Option: its TETI index, TTGP layer name, and color presets."""

    __slots__ = ('index', 'name', 'colors')

    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name
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
        """Parse a race's Male/Female tint Options, grouped by sex + lowercased
        TTGP layer name (every named option is kept; categories are applied
        later from the catalog's [tint_categories]).

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
                if name:
                    opt = TintOption(slot_index, name)
                    by_sex[section][name.lower()].append(opt)
                else:
                    opt = None  # unnamed option: ignore
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


    def options_by_name(self, race_edid: str, sex: Sex) -> dict:
        """{layer_name_lower: [TintOption]} for a race+sex (empty if none)."""
        return self._by_race.get(race_edid, {}).get(sex, {})


    def rgb(self, clfm_fid: int) -> Optional[tuple]:
        return self._clfm_rgb.get(clfm_fid)


    def pick_from(self, options: list, signature: str, seed: int,
                  allow_zero: bool = False, color_rule=None):
        """Pick (option, (clfm_fid, alpha, tmpl)) from a list of TintOptions, or
        None.

        Choose one option, then a color preset with alpha > 0 (`allow_zero` for
        skin tone). When `color_rule` (a customization.ColorRule) has a non-empty
        palette, restrict to its allowed CLFM EditorIDs and override alpha with
        the scheme's intensity. Returns None if nothing usable.
        """
        if not options:
            return None
        option = options[hash_string(signature, seed, len(options))]
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


def resolve_options(categories: dict, names: dict, key_lower: str) -> list:
    """Resolve a scheme/default key to a list of TintOptions: a CATEGORY (all
    its matching layers) or, failing that, an EXACT layer name (just that one).
    `categories` is {category_key_lower: [glob patterns]}, `names` is
    `RaceTints.options_by_name` ({layer_lower: [TintOption]}). Shared by
    apply_tints and catalog validation so both resolve identically."""
    pats = categories.get(key_lower)
    if pats is not None:
        out = []
        for nm_lower, opts in names.items():
            if any(wildcard_match(p, nm_lower) for p in pats):
                out.extend(opts)
        return out
    return list(names.get(key_lower, []))


def apply_tints(patch, ov: Record, race_edid: str, sex: Sex,
                signature: str, race_tints: RaceTints,
                color_scheme=None, categories=None) -> int:
    """Write face-tint layers onto a furrified NPC override. Returns count.

    `categories` is the race's file-scoped {category_key_lower: [patterns]}.
    A scheme/default key resolves to a CATEGORY (random one of its matching
    layers) or, failing that, an EXACT layer name (just that layer).

    Skin Tone is always applied first (seeds QNAM), gated only by an explicit
    scheme probability < 1. Then: with a `color_scheme`, apply exactly the keys
    it lists, in order, each at its own probability; without one, apply each
    defined category (in file order) at the ~65% default gate. No cap either way.
    """
    categories = categories or {}
    names = race_tints.options_by_name(race_edid, sex)
    if not names:
        return 0

    def resolve(key_lower: str) -> list:
        return resolve_options(categories, names, key_lower)

    # Scheme keys, lowercased (so `Muzzle_Stripe`/`Muzzle Stripe` resolve the
    # same), order preserved.
    scheme = {k.lower(): v for k, v in color_scheme.items()} if color_scheme \
        else None

    written = 0
    # Layer indices already written. Two scheme keys can resolve to the same
    # layer — e.g. a direct `Muzzle_Upper` key AND the `Muzzle_Stripe` category
    # (which lists "Muzzle Upper" among its members) both landing on it — and
    # _write_layer just appends, so without this the NPC gets the layer twice.
    # Dedup by layer index, first key in scheme order wins.
    seen: set = set()

    # Skin tone: always attempted first, seeds QNAM.
    skin_rule = scheme.get(SKIN_TONE_KEY) if scheme else None
    if skin_rule is None or skin_rule.probability >= 1.0 or \
            hash_string(signature, 9001, 100) < skin_rule.probability * 100:
        picked = race_tints.pick_from(resolve(SKIN_TONE_KEY), signature, 9523,
                                      allow_zero=True, color_rule=skin_rule)
        if picked is not None:
            option, color = picked
            _write_layer(ov, option, color, race_tints)
            _set_qnam(ov, race_tints.rgb(color[0]), color[1])
            seen.add(option.index)
            written += 1

    # Everything else, in order. Scheme keys win; else the file's categories.
    if scheme is not None:
        items = [(k, r) for k, r in scheme.items() if k != SKIN_TONE_KEY]
    else:
        items = [(k, None) for k in categories if k != SKIN_TONE_KEY]

    for i, (key_lower, rule) in enumerate(items):
        gate = rule.probability * 100 if rule is not None else _DEFAULT_GATE
        if hash_string(signature, 2189 + i * 53, 100) >= gate:
            continue
        picked = race_tints.pick_from(resolve(key_lower), signature,
                                      1783 + i * 41, color_rule=rule)
        if picked is None:
            continue
        option, color = picked
        if option.index in seen:
            continue  # already written by an earlier key (e.g. Muzzle_Upper)
        _write_layer(ov, option, color, race_tints)
        seen.add(option.index)
        written += 1

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
