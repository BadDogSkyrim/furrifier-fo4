"""Golden CK-parity test for the facegen export — see PLAN_FACEGEN_SHADER_PARITY.md.

Drives the furrifier through the FRONT DOOR (`main()` with a real argv), baking
John (deer) and Rosalind (fox) from committed fixtures via `--resources`, and
diffs every baked shape against a committed CK reference facegeom — shader type,
all shader flags, textures, skin tint, rim/backlight, specular, root material,
vertex colors, skin-instance type, bone transforms (incl. scale), and cloth-data
attachment. One failing test lists every discrepancy instead of finding them by
eye in-game.

`@pytest.mark.gamefiles`: needs FO4 + the DLCs + FurryFallout.esp installed (for
the NPC/race records). The geometry + materials under test come from fixtures;
incidental assets (base textures, skeleton, tri) fall back to the game Data.
"""

from __future__ import annotations

import sys
from ctypes import c_int, c_char, byref
from pathlib import Path

import pytest

from furrifier_fo4._pyn import ensure_dev_path

ensure_dev_path()
from pyn.pynifly import NifFile  # noqa: E402
from pyn.niflydll import nifly  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"
_REF = _FIX / "ck_reference"
_OUT = Path(__file__).parent / "output" / "facegen_parity"  # gitignored, kept

# NPC -> (baked facegeom relpath under the output dir, CK reference filename).
# Both are Fallout4.esm records, so the engine reads their facegen from the
# base-record (Fallout4.esm) folder.
_FACEGEOM = "Meshes/Actors/Character/FaceGenData/FaceGeom/Fallout4.esm"

# Per-NPC: baked facegeom relpath, CK reference filename, the head (Face) shape
# name, and the SHARED race head Normal/Specular maps we deliberately use on the
# head instead of CK's per-NPC `_msn`/`_s` (a VRAM optimisation — see the head
# texture policy in the test). Lowercased to match `_shape_props`.
_NPCS = {
    "John": {
        "baked": f"{_FACEGEOM}/00002CCB.nif", "ck": "John_CK_00002CCB.nif",
        "head_shape": "FFODeerMaleHead",
        "head_normal": "textures\\ffo\\deer\\head\\deermalehead_n.dds",
        "head_specular": "textures\\ffo\\deer\\head\\deermalehead_s.dds",
    },
    "RosalindOrman": {
        "baked": f"{_FACEGEOM}/0005E562.nif", "ck": "Rosalind_CK_0005E562.nif",
        "head_shape": "FFOFoxFemHead",
        "head_normal": "textures\\ffo\\lykaios\\head\\lykaiosfemalehead_n.dds",
        "head_specular": "textures\\ffo\\lykaios\\head\\lykaiosfemalehead_s.dds",
    },
}

_EYE_ENV = 0x20000  # ShaderFlags1FO4.EYE_ENVIRONMENT_MAPPING


def _vec(v, n):
    # 3 decimals: tight enough to catch real colour/position diffs, loose enough
    # to ignore float jitter from the height-scale multiply (sub-0.001).
    return tuple(round(v[i], 3) for i in range(n))


def _xf(t):
    # Raw floats — compared with a tolerance (see _bones_close), not rounded:
    # the per-bone height-scale multiply differs by float epsilon between the CK
    # and our bake, which rounding straddles unpredictably at .xx5 boundaries.
    rot = tuple(tuple(c for c in row) for row in t.rotation)
    tr = tuple(t.translation[i] for i in range(3))
    return (tr, rot, getattr(t, "scale", 1.0))


def _bones_close(ours: dict, ck: dict, tol: float = 0.02) -> bool:
    """Per-bone transform compare with a tolerance. A real placement bug is
    ~units off (the unscaled-height bug was ~2.4); tol ignores float jitter."""
    if set(ours) != set(ck):
        return False

    def flat(xf):
        tr, rot, sc = xf
        return [*tr, *(c for row in rot for c in row), sc]

    return all(
        all(abs(a - b) <= tol for a, b in zip(flat(ours[k]), flat(ck[k])))
        for k in ck)


def _cloth_count(nif, shape):
    nl, vl = c_int(), c_int()
    return sum(1 for i in range(64)
               if nifly.getClothExtraDataLen(nif._handle, shape._handle, i,
                                              byref(nl), byref(vl)))


def _shape_props(nif, shape) -> dict:
    """Everything we assert parity on for one shape."""
    p = shape.shader.properties
    sp = shape.shader._properties
    tex = {}
    raw = shape.textures
    for k, v in (raw.items() if hasattr(raw, "items") else enumerate(raw)):
        if v:
            tex[str(k)] = v.lower()
    bones = {b: _xf(shape.get_shape_skin_to_bone(b)) for b in shape.bone_names}
    nodes = {b: _xf(nif.nodes[b].transform) for b in shape.bone_names
             if b in nif.nodes}
    return {
        "shader_type": sp.Shader_Type,
        "flags1": sp.Shader_Flags_1,
        "flags2": sp.Shader_Flags_2,
        "textures": tex,
        "skin_tint": _vec(sp.skinTintColor, 3),
        "skin_tint_alpha": round(sp.Skin_Tint_Alpha, 4),
        "rim_power": round(sp.Rim_Light_Power, 4),
        "backlight": round(sp.backlightPower, 4),
        "spec_color": _vec(sp.Spec_Color, 3),
        "spec_str": round(sp.Spec_Str, 4),
        # No raw rootMaterialNameID: NONE (ours) and string-index-0 (CK) both mean
        # "no root material". The resolved RootMaterialPath (in `textures`, empty
        # either way) is the meaningful comparison.
        "colors": len(shape.colors) if shape.colors else 0,
        "skin_instance": shape.skin_instance_name,
        "skin_to_bone": bones,
        "bone_nodes": nodes,
        "cloth_blocks": _cloth_count(nif, shape),
    }


def _all_shapes(path) -> dict:
    n = NifFile(str(path))
    return {s.name: _shape_props(n, s) for s in n.shapes}


@pytest.fixture(scope="module")
def baked():
    """Bake John + Rosalind through the CLI front door, once."""
    argv = ["furrify-fo4", "--npcs", "John,RosalindOrman",
            "--scheme", "test_facegen", "--resources", str(_FIX),
            "-o", str(_OUT)]
    old = sys.argv
    sys.argv = argv
    try:
        from furrifier_fo4.main import main
        rc = main()
    finally:
        sys.argv = old
    assert rc == 0, f"furrifier run failed (rc={rc})"
    return _OUT


@pytest.mark.gamefiles
@pytest.mark.parametrize("npc", list(_NPCS))
def test_facegen_matches_ck(baked, npc):
    cfg = _NPCS[npc]
    ours = _all_shapes(baked / cfg["baked"])
    ck = _all_shapes(_REF / cfg["ck"])

    assert set(ours) == set(ck), (
        f"{npc}: shape set differs\n  ours={sorted(ours)}\n  ck  ={sorted(ck)}")

    diffs = []
    for name in sorted(ck):
        o, c = ours[name], ck[name]
        is_head = name == cfg["head_shape"]
        is_eye = c["shader_type"] == 1   # Environment-Map type = the eyes
        for field, ck_val in c.items():
            our_val = o.get(field)

            if field in ("bone_nodes", "skin_to_bone"):
                if not _bones_close(our_val, ck_val):
                    diffs.append(f"  {name}.{field}: (beyond tolerance)")
                continue

            # Skin-tint alpha: the colour matches exactly (QNAM == CK), but the
            # alpha is the QNAM's 0.9 vs CK's 0.898 (229/255) — a tint-alpha
            # encoding nuance, visually identical. Tolerance, don't chase 0.002.
            if field == "skin_tint_alpha":
                if abs((our_val or 0) - (ck_val or 0)) > 0.01:
                    diffs.append(f"  {name}.skin_tint_alpha:\n     ours={our_val}\n     ck  ={ck_val}")
                continue

            # Eyes: preserve the modder's EYE_ENVIRONMENT_MAPPING — CK strips it,
            # we keep the source. Mask just that bit.
            if field == "flags1" and is_eye:
                if (our_val & ~_EYE_ENV) != (ck_val & ~_EYE_ENV):
                    diffs.append(f"  {name}.flags1 (eye-env masked):\n"
                                 f"     ours=0x{our_val:08x}\n     ck  =0x{ck_val:08x}")
                continue

            if field == "textures":
                ot, ct = dict(our_val), dict(ck_val)
                if is_head:
                    # Head Normal/Specular: we deliberately use the SHARED race
                    # maps, not CK's per-NPC _msn/_s. Assert our intended values,
                    # then drop them from the CK comparison.
                    for slot, exp in (("Normal", cfg["head_normal"]),
                                      ("Specular", cfg["head_specular"])):
                        if ot.get(slot) != exp:
                            diffs.append(f"  {name}.textures[{slot}] (shared map):"
                                         f"\n     ours={ot.get(slot)}\n     want={exp}")
                        ot.pop(slot, None)
                        ct.pop(slot, None)
                elif is_eye:
                    # Eyes: preserve the modder's source maps; CK substitutes its
                    # own eyegloss/eyeenvironmentmask. Mask Normal/Specular.
                    for slot in ("Normal", "Specular"):
                        ot.pop(slot, None)
                        ct.pop(slot, None)
                if ot != ct:
                    diffs.append(f"  {name}.textures:\n     ours={ot}\n     ck  ={ct}")
                continue

            if our_val != ck_val:
                diffs.append(f"  {name}.{field}:\n     ours={our_val}\n     ck  ={ck_val}")
    assert not diffs, f"{npc}: {len(diffs)} field(s) differ from CK:\n" + "\n".join(diffs)
