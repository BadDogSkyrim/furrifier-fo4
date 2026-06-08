"""Unit tests for facebone (region) LBS baking: the slider->bone-delta matrix,
the skin deformation, and the JSON region-by-FMRI lookup. Pure — synthetic
shapes + a temp JSON, no game files."""

import json
import math

import numpy as np

from furrifier_fo4.facegen.facebones import (
    bone_delta_matrix, facebone_displacements, _interp)
from furrifier_fo4.facemorphs import FacialBoneRegions
from furrifier_fo4.models import Sex


def _region(pos=(0, 0, 0), rot=(0, 0, 0), scale=(0, 0, 0)):
    def axes(t):
        return {"x": t[0], "y": t[1], "z": t[2]}
    return {"Position": axes(pos), "Rotation": axes(rot), "Scale": axes(scale)}


# ----------------------------------------------------- slider interpolation ---

def test_interp_through_rest_zero():
    assert _interp(0.0, -4, 4) == 0.0
    assert _interp(0.5, -4, 4) == 2.0       # toward maxima
    assert _interp(-0.5, -4, 4) == -2.0     # toward minima
    assert _interp(1.0, -4, 4) == 4.0


# ----------------------------------------------------- bone delta matrix ------

def test_delta_translation():
    mn = _region(pos=(0, -4, 0))
    mx = _region(pos=(0, 4, 0))
    D = bone_delta_matrix(mn, mx, (0, 0.5, 0, 0, 0, 0, 0))
    assert np.allclose(D[:3, 3], [0, 2.0, 0])      # y slider 0.5 -> +2
    assert np.allclose(D[:3, :3], np.eye(3))        # no rot/scale


def test_delta_scale_is_one_plus_offset():
    mn = _region(scale=(-0.5, -0.5, -0.5))
    mx = _region(scale=(0.5, 0.5, 0.5))
    D = bone_delta_matrix(mn, mx, (0, 0, 0, 0, 0, 0, 1.0))   # scale slider +1
    assert np.allclose(D[:3, :3], np.eye(3) * 1.5)  # factor 1 + 0.5
    assert np.allclose(D[:3, 3], 0)


def test_delta_rotation_degrees_to_radians():
    mn = _region(rot=(-20, 0, 0))
    mx = _region(rot=(20, 0, 0))
    D = bone_delta_matrix(mn, mx, (0, 0, 0, 1.0, 0, 0, 0))   # rotX slider +1 = 20deg
    a = math.radians(20)
    expected = np.array([[1, 0, 0],
                         [0, math.cos(a), -math.sin(a)],
                         [0, math.sin(a), math.cos(a)]])
    assert np.allclose(D[:3, :3], expected)


# ----------------------------------------------------- LBS deformation --------

class _Buf:
    def __init__(self, rot, trans, scale=1.0):
        self.rotation = rot
        self.translation = trans
        self.scale = scale


class _Shape:
    def __init__(self, verts, s2b, weights):
        self._verts = verts
        self.bone_names = set(s2b)
        self._s2b = s2b
        self.bone_weights = weights

    @property
    def verts(self):
        return self._verts

    def get_shape_skin_to_bone(self, b):
        return self._s2b[b]


_IDENT = _Buf([[1, 0, 0], [0, 1, 0], [0, 0, 1]], (0, 0, 0), 1.0)


def test_displacement_identity_s2b_translates_weighted_vert():
    # s2b identity -> M == D; a vert fully weighted to the bone moves by D's
    # translation, an unweighted one stays.
    shape = _Shape([(0.0, 0.0, 0.0), (5.0, 5.0, 5.0)],
                   {"skin_bone_n": _IDENT},
                   {"skin_bone_n": [(0, 1.0), (1, 0.0)]})
    D = np.eye(4)
    D[:3, 3] = (2.0, 0.0, 0.0)
    disp = facebone_displacements(shape, {"bone_n": D.tolist()})   # bare -> skin_ mapped
    assert np.allclose(disp[0], [2.0, 0.0, 0.0])
    assert np.allclose(disp[1], [0.0, 0.0, 0.0])


def test_displacement_weight_scales_contribution():
    shape = _Shape([(0.0, 0.0, 0.0)], {"skin_bone_n": _IDENT},
                   {"skin_bone_n": [(0, 0.25)]})
    D = np.eye(4)
    D[:3, 3] = (4.0, 0.0, 0.0)
    disp = facebone_displacements(shape, {"bone_n": D.tolist()})
    assert np.allclose(disp[0], [1.0, 0.0, 0.0])   # 0.25 * 4


class _Skel:
    def __init__(self, world, children):
        self.world = world
        self._children = children

    def descendants(self, bone):
        out, stack = [], list(self._children.get(bone, []))
        while stack:
            b = stack.pop()
            out.append(b)
            stack.extend(self._children.get(b, []))
        return out


def test_displacement_parent_control_bone_via_skeleton():
    # The region drives a PARENT control bone (bone_L_Ear); the head skins to
    # its child (skin_bone_L_EarTop). With identity binds, the child's verts move
    # by the parent's delta — and without the skeleton it can't resolve at all.
    shape = _Shape([(0.0, 0.0, 0.0), (5.0, 5.0, 5.0)],
                   {"skin_bone_L_EarTop": _IDENT},
                   {"skin_bone_L_EarTop": [(0, 1.0), (1, 0.0)]})
    skel = _Skel(world={"skin_bone_L_Ear": np.eye(4),
                        "skin_bone_L_EarTop": np.eye(4)},
                 children={"skin_bone_L_Ear": ["skin_bone_L_EarTop"]})
    D = np.eye(4)
    D[:3, 3] = (3.0, 0.0, 0.0)
    disp = facebone_displacements(shape, {"bone_L_Ear": D.tolist()}, skeleton=skel)
    assert np.allclose(disp[0], [3.0, 0.0, 0.0])   # child moved by the parent delta
    assert np.allclose(disp[1], [0.0, 0.0, 0.0])
    # no skeleton -> the control bone isn't a skin bone -> nothing moves
    disp2 = facebone_displacements(shape, {"bone_L_Ear": D.tolist()})
    assert np.allclose(disp2[0], [0.0, 0.0, 0.0])


def test_displacement_skips_bone_absent_from_skin():
    shape = _Shape([(0.0, 0.0, 0.0)], {"skin_bone_n": _IDENT},
                   {"skin_bone_n": [(0, 1.0)]})
    D = np.eye(4)
    D[:3, 3] = (9.0, 0.0, 0.0)
    disp = facebone_displacements(shape, {"bone_other": D.tolist()})
    assert np.allclose(disp[0], [0.0, 0.0, 0.0])    # not in skin -> no move


# ----------------------------------------------------- region-by-FMRI lookup --

def test_bones_for_fmri_indexes_by_json_id(tmp_path):
    regions = [
        {"Name": "Ears - Full", "ID": 5, "AssociatedMorphGroup": "Neck",
         "BonesA": [{"Bone": "bone_R_EarTop",
                     "Minima": _region(pos=(2, -2, -2)),
                     "Maxima": _region(pos=(-2, 2, 2))}],
         "BonesB": None},
        {"Name": "Nose - Full", "ID": 9, "AssociatedMorphGroup": "Nose",
         "BonesA": [{"Bone": "bone_C_MasterNose",
                     "Minima": _region(pos=(0, -4, -4)),
                     "Maxima": _region(pos=(0, 4, 4))}]},
    ]
    sub = tmp_path / "Meshes" / "Actors" / "Character" / "CharacterAssets"
    sub.mkdir(parents=True)
    (sub / "FFOFoxRaceFacialBoneRegionsMale.txt").write_text(json.dumps(regions))

    br = FacialBoneRegions(tmp_path)
    bones = br.bones_for_fmri("FFOFoxRace", Sex.MALE, 5)   # ID 5 == FMRI 5
    assert [b[0] for b in bones] == ["bone_R_EarTop"]
    assert br.bones_for_fmri("FFOFoxRace", Sex.MALE, 9)[0][0] == "bone_C_MasterNose"
    assert br.bones_for_fmri("FFOFoxRace", Sex.MALE, 999) == []
    # name lookup (AssociatedMorphGroup) still works
    assert br.associated_group("FFOFoxRace", Sex.MALE, "Ears - Full") == "Neck"
