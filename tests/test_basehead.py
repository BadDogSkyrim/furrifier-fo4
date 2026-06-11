"""Regression for BaseHeadTextures: the head HDPT's TNAM->TXST override is the
authoritative per-race head texture and must win over the nif's material.

FFO's furry child heads all share the generic vanilla `childmalehead.BGSM`, so
reading the nif material gave every species the same (wolf-like) child diffuse —
a deer child rendered as Lykaios. The per-species texture lives in the HDPT TXST
(e.g. FFODeerChildHead -> DeerChildHead_d.dds), which is what the CK bakes from.
"""

from furrifier_fo4.facegen.basehead import BaseHeadTextures
from furrifier_fo4.models import Sex


class _Sub:
    def __init__(self, signature, data=b"", fid=None):
        self.signature = signature
        self.data = data
        self.size = len(data)
        self._fid = fid

    def get_form_id(self):
        return self._fid


class _HDPT:
    plugin = None  # _texture_overrides passes hdpt.plugin to resolve_form_id

    def __init__(self, subs):
        self.subrecords = subs

    def get_subrecord(self, sig):
        return next((s for s in self.subrecords if s.signature == sig), None)


class _TXST:
    # A TXST whose TX00 (diffuse) names the per-species deer child head texture.
    subrecords = [_Sub("TX00", b"FFO\\Deer\\Head\\DeerChildHead_d.dds\x00")]


class _PluginSet:
    def resolve_form_id(self, fid, plugin):
        return _TXST()


class _Pools:
    plugin_set = _PluginSet()

    def __init__(self, hp):
        self._hp = hp

    def pool(self, race, sex, type_name):
        return [self._hp] if type_name == "Face" else []


def test_base_head_prefers_hdpt_txst_over_nif_material():
    # The HDPT has a TNAM (-> TXST diffuse); base_heads must use it, never
    # touching the nif material (so resolver=None is safe here).
    hp = _HDPT([_Sub("TNAM", b"\x00\x00\x00\x00", fid=0x123),
                _Sub("MODL", b"FFO\\Deer\\DeerChildHead.nif\x00")])
    bh = BaseHeadTextures(_Pools(hp), resolver=None)
    out = bh.get("FFODeerChildRace", Sex.MALE)
    assert out is not None
    assert out["diffuse"] == "textures\\FFO\\Deer\\Head\\DeerChildHead_d.dds"


def test_base_head_uses_race_default_when_pool_empty():
    # Tiger/snekdog children: their FLST-based Face pool is EMPTY (FFO lists the
    # child head only on the adult race's FLST), but the RACE record defines a
    # default head. base_heads must read that default head's TXST rather than
    # returning None, which would skip the bake and crash the previewer.
    head = _HDPT([_Sub("PNAM", b"\x01\x00\x00\x00"),            # type 1 = Face
                  _Sub("TNAM", b"\x00\x00\x00\x00", fid=0x66),  # -> TXST diffuse
                  _Sub("MODL", b"FFO\\Tiger\\TigChildHead.nif\x00")])

    class _FidPS:
        def resolve_form_id(self, fid, plugin):
            return head if fid == 0x55 else _TXST()

    class _EmptyPools:
        plugin_set = _FidPS()

        def pool(self, race, sex, type_name):
            return []   # FLST pool empty for the child race

    class _Race:
        plugin = None
        # Male head-data section (NAM0 #1) with one HEAD -> the default head.
        subrecords = [_Sub("NAM0", b"\x01\x00\x00\x00"),
                      _Sub("HEAD", b"\x00\x00\x00\x00", fid=0x55)]

    bh = BaseHeadTextures(_EmptyPools(), resolver=None,
                          races_by_edid={"FFOTigerChildRace": _Race()})
    out = bh.get("FFOTigerChildRace", Sex.MALE)
    assert out is not None   # was None before the fix -> no_base -> no nif
    assert out["diffuse"] == "textures\\FFO\\Deer\\Head\\DeerChildHead_d.dds"
