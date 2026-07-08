"""End-to-end facegeom assembly: generate a facegen nif from synthesized
source head parts and assert it's free of every bug class we've hit in game.

The whole nif-assembly path (`build_facegen_nif`) had no coverage — the other
facegen tests only exercise the DDS/texture side. This builds a real facegeom
through the real code and checks the structural invariants that, when violated,
crashed or corrupted the head in-game:

  - SKINNED shader flag MUST be set on every shape. FFO source heads don't carry
    it; without it the engine runs the non-skinned vertex path over skinned data
    and crashes d3d11 (Addictol bFacegen CTD). We force it during bake.
  - Every OTHER shader flag (e.g. FACE on the head) must survive the wholesale
    property copy — only SKINNED is added, nothing is dropped.
  - Skin data — bones, skin-to-bone transforms (incl. ROTATION), weights — copied
    verbatim. A mangled skin-to-bone rotation warps the face.
  - Bone NiNode stubs declared BEFORE the BSFaceGenNiNodeSkinned (Skyrim/FO4 nif
    loaders parse linearly; shapes reference bones by id).
  - Material name cleared (a lingering BGSM ref overrides the inline
    FaceCustomization textures the head points at).
  - Face part's diffuse stamped to the per-NPC FaceCustomization path.
  - Greyscale-to-palette hair gets its grayscaleToPaletteScale (the NPC's hair
    colour) so it isn't rendered as raw grayscale.

Pure test: source nifs are synthesized with PyNifly, no game install needed.
"""

from __future__ import annotations

import pytest

from furrifier_fo4._pyn import ensure_dev_path

ensure_dev_path()
from pyn.pynifly import NifFile  # noqa: E402
from pyn.structs import TransformBuf  # noqa: E402
from pyn.nifdefs import PynBufferTypes  # noqa: E402
from pyn.nifconstants import ShaderFlags1FO4  # noqa: E402

from furrifier_fo4.facegen.assets import AssetResolver  # noqa: E402
from furrifier_fo4.facegen.assemble import build_facegen_nif  # noqa: E402
from furrifier_fo4.facegen.headparts_resolve import HDPT_FACE  # noqa: E402

_SITS = PynBufferTypes.BSSubIndexTriShapeBufType
# A non-identity skin-to-bone rotation (90deg about Z) — must round-trip exactly.
_S2B_ROT = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
# A non-identity bone bind-pose rotation (FO4 keeps rotation, unlike Skyrim).
_BONE_ROT = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
HAIR_PALETTE_SCALE = 0.7


def _xf(translation=(0.0, 0.0, 0.0), rotation=None):
    t = TransformBuf()
    t.set_identity()
    t.translation = translation
    if rotation is not None:
        for i in range(3):
            for j in range(3):
                t.rotation[i][j] = rotation[i][j]
    return t


def _add_skinned_shape(nif, name, verts, tris, *, shader_type, flags,
                       s2b, colors=None, diffuse=None):
    """Create one skinned BSSubIndexTriShape with an inline (no-BGSM) shader
    whose flags are exactly `flags` (SKINNED deliberately excluded by callers)."""
    uvs = [(0.0, 0.0)] * len(verts)
    normals = [(0.0, 0.0, 1.0)] * len(verts)
    shp = nif.createShapeFromData(name, verts, tris, uvs, normals, use_type=_SITS)
    shp.flags = 14  # NiAVObject flags as FFO/CK carry them
    if colors:
        shp.set_colors(colors)
    shp.skin()
    shp.add_bone("HEAD")  # miscased on purpose (FFO source); bake must canonicalize
    shp.set_skin_to_bone_xform("HEAD", s2b)
    shp.setShapeWeights("HEAD", [(i, 1.0) for i in range(len(verts))])
    p = shp.shader.properties
    p.Shader_Type = shader_type
    p.shaderflags1_clear(ShaderFlags1FO4.SKINNED)  # FFO heads ship without it
    for f in flags:
        p.shaderflags1_set(f)
    shp.shader.name = ""  # no BGSM -> inline path
    shp.save_shader_attributes()
    if diffuse:
        shp.set_texture("Diffuse", diffuse)
    return p.Shader_Flags_1


def _make_source(path, shapes_spec):
    nif = NifFile()
    nif.initialize("FO4", str(path), root_type="NiNode", root_name="scene")
    nif.add_node("HEAD", _xf(translation=(1.0, 2.0, 3.0), rotation=_BONE_ROT),
                 parent=nif.root)
    flags = {}
    for spec in shapes_spec:
        flags[spec["name"]] = _add_skinned_shape(nif, **spec)
    nif.save()
    return flags


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Synthesize a head nif + a hair nif, assemble one facegeom, and return
    handles for the assertions below (built once, asserted many times)."""
    tmp = tmp_path_factory.mktemp("fo4_facegen")

    head_rel = "meshes\\ffo\\test\\head.nif"
    hair_rel = "meshes\\ffo\\test\\hair.nif"
    head_path = _loose(tmp, head_rel)
    hair_path = _loose(tmp, hair_rel)

    src_flags = {}
    src_flags.update(_make_source(head_path, [dict(
        name="FFOTestHead", verts=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        tris=[(0, 1, 2), (0, 2, 3)], shader_type=4,
        flags=[ShaderFlags1FO4.FACE, ShaderFlags1FO4.SPECULAR,
               ShaderFlags1FO4.CAST_SHADOWS],
        s2b=_xf(translation=(-1.0, -2.0, -3.0), rotation=_S2B_ROT),
        colors=[(1.0, 1.0, 1.0, 1.0)] * 4,
        diffuse="textures\\ffo\\test\\head_d.dds")]))
    src_flags.update(_make_source(hair_path, [dict(
        name="FFOTestHair", verts=[(0, 0, 1), (1, 0, 1), (1, 1, 1)],
        tris=[(0, 1, 2)], shader_type=6,
        flags=[ShaderFlags1FO4.GREYSCALETOPALETTE_COLOR],
        s2b=_xf())]))

    out = tmp / "out" / "00ABCDEF.nif"
    headparts = [
        {"source_nif": head_rel, "hdpt_type": HDPT_FACE},
        {"source_nif": hair_rel, "hdpt_type": 0},  # non-Face
    ]
    with AssetResolver(tmp, bsa_readers=[]) as resolver:
        ok = build_facegen_nif("00ABCDEF", "TestFFO.esp", headparts, resolver,
                               out, hair_palette_scale=HAIR_PALETTE_SCALE)
    assert ok, "build_facegen_nif returned False"

    nif = NifFile(str(out))
    shapes = {s.name: s for s in nif.shapes}
    return {"nif": nif, "shapes": shapes, "src_flags": src_flags}


def _loose(root, relpath):
    p = root / relpath.replace("\\", "/")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _f1(shape):
    shape.shader.properties
    return shape.shader._properties.Shader_Flags_1


# --------------------------------------------------------------------------- #

def test_root_is_bsfadenode(built):
    """CK emits a BSFadeNode root (flags 0x2000400E, empty name), not a NiNode."""
    nif = built["nif"]
    assert nif.root.blockname == "BSFadeNode"
    assert nif.root.flags == 0x2000400E
    assert nif.root.name == ""


def test_all_shapes_are_subindex_trishape(built):
    for name, s in built["shapes"].items():
        assert s.blockname == "BSSubIndexTriShape", name


def test_every_shape_has_skinned_flag(built):
    """The CTD guard: SKINNED must be set on every shape, even though the
    sources cleared it."""
    for name, s in built["shapes"].items():
        assert _f1(s) & ShaderFlags1FO4.SKINNED, f"{name} missing SKINNED"


def test_skinned_is_the_only_flag_added(built):
    """Every other flag survives the wholesale copy — output == source | SKINNED.
    (Guards the 'we lost FACE' class of bug.)"""
    for name, s in built["shapes"].items():
        expected = built["src_flags"][name] | ShaderFlags1FO4.SKINNED.value
        assert _f1(s) == expected, (
            f"{name}: F1=0x{_f1(s):08x} expected 0x{expected:08x} "
            f"(source 0x{built['src_flags'][name]:08x} | SKINNED)")


def test_face_flag_preserved_on_head(built):
    head = built["shapes"]["FFOTestHead"]
    assert _f1(head) & ShaderFlags1FO4.FACE, "FACE dropped on head"


def test_head_output_shader_type_is_face(built):
    """The head shape must bake as Face Tint (4), not Default (0). FFO's
    FoxFemaleHead BGSM sets the `facegen` flag (not skinTint), which
    _facegen_shader_type maps to Face Tint; a Default head renders with no
    skin/subsurface shading in-game (the Rosalind regression)."""
    head = built["shapes"]["FFOTestHead"]
    head.shader.properties
    assert head.shader._properties.Shader_Type == 4  # Face Tint


def test_material_name_cleared(built):
    for name, s in built["shapes"].items():
        assert s.shader.name == "", f"{name} kept material ref {s.shader.name!r}"


def test_skin_data_copied_verbatim(built):
    """Bones, weights, and the skin-to-bone transform (incl. rotation) survive."""
    head = built["shapes"]["FFOTestHead"]
    assert head.bone_names == ["Head"]
    assert len(head.bone_weights["Head"]) == 4
    s2b = head.get_shape_skin_to_bone("Head")
    assert tuple(s2b.translation) == pytest.approx((-1.0, -2.0, -3.0))
    for i in range(3):
        for j in range(3):
            assert s2b.rotation[i][j] == pytest.approx(_S2B_ROT[i][j]), (i, j)


def test_vertex_colors_preserved(built):
    head = built["shapes"]["FFOTestHead"]
    assert head.colors and len(head.colors) == 4


def test_bone_rotation_zeroed_translation_kept(built):
    """Match CK: bone nodes keep their bind-pose TRANSLATION but rotation is
    zeroed to identity (positioning happens via the skin). The source bone had a
    non-identity rotation (_BONE_ROT); the output must not."""
    import numpy as np
    bone = built["nif"].nodes["Head"]
    assert tuple(bone.transform.translation) == pytest.approx((1.0, 2.0, 3.0))
    rot = np.array([[bone.transform.rotation[i][j] for j in range(3)]
                    for i in range(3)])
    assert np.allclose(rot, np.eye(3)), f"bone rotation not identity:\n{rot}"


def test_head_shape_transform_is_identity(built):
    """Match CK: the shape's local transform is identity (no baked +120)."""
    import numpy as np
    head = built["shapes"]["FFOTestHead"]
    assert tuple(head.transform.translation) == pytest.approx((0.0, 0.0, 0.0))
    rot = np.array([[head.transform.rotation[i][j] for j in range(3)]
                    for i in range(3)])
    assert np.allclose(rot, np.eye(3))


def test_facegen_shader_type_from_material():
    """CK derives the facegen shader type from the BGSM, fixing FFO's
    mis-authored source types. Cases from Malcolm (Deer male, 0x2F10)."""
    from types import SimpleNamespace
    from furrifier_fo4.facegen.assemble import _facegen_shader_type as f

    def mat(**kw):
        kw.setdefault("glowmap", False)
        kw.setdefault("skinTint", False)
        kw.setdefault("facegen", False)
        return SimpleNamespace(**kw)

    # glowmap -> Glow(2), regardless of the source type (THE crash fix: hair)
    assert f(mat(glowmap=True), 0) == 2   # FFO hair authored Default -> Glow
    assert f(mat(glowmap=True), 2) == 2   # neck gore already Glow -> stays
    # facegen -> Face Tint(4): the head BGSM sets `facegen` (not skinTint); that
    # flag means Face Tint and drives the type even from a Default source. This is
    # the FoxFemaleHead/Rosalind fix — without it the head baked as Default(0).
    assert f(mat(facegen=True), 4) == 4   # head: source already Face -> stays
    assert f(mat(facegen=True), 0) == 4   # facegen promotes a Default source
    # non-tinted Face/Skin-Tint source -> Default (FFO horns authored Face_Tint)
    assert f(mat(), 4) == 0                # horns Face_Tint, no flags -> Default
    # skin-tinted keeps its tint type (e.g. furry horn base = Skin_Tint)
    assert f(mat(skinTint=True), 5) == 5   # horn base stays Skin_Tint
    # untouched types pass through (eyes EnvMap=1, mouth Default=0)
    assert f(mat(), 1) == 1
    assert f(mat(), 0) == 0
    # no material -> unchanged
    assert f(None, 4) == 4


def test_apply_material_shading_skips_missing_fields():
    """Specular/backlight come from the BGSM material — but an effect (BGEM)
    material (e.g. on some NPCs' hair) lacks those fields. The copy must skip
    them, not throw (a throw failed the whole NPC bake — 18 nifs in single_race)."""
    from furrifier_fo4.facegen.assemble import _apply_material_shading
    from types import SimpleNamespace

    class Props:
        def __init__(self):
            self.Spec_Color = [0.0, 0.0, 0.0]
            self.Spec_Str = 0.0
            self.backlightPower = 0.0

    p = Props()  # full BGSM-like material
    _apply_material_shading(p, SimpleNamespace(
        specularColor=[1.0, 1.0, 1.0], specularMult=3.0, backlightPower=2.0))
    assert p.Spec_Color == [1.0, 1.0, 1.0]
    assert p.Spec_Str == 3.0 and p.backlightPower == 2.0

    p = Props()  # effect material missing the BGSM fields -> no throw, unchanged
    _apply_material_shading(p, SimpleNamespace(glowmap=True))
    assert p.Spec_Color == [0.0, 0.0, 0.0]
    assert p.Spec_Str == 0.0 and p.backlightPower == 0.0


def test_face_flag_stripped_from_non_face_part(tmp_path):
    """FFO authors some non-face parts with the FACE shader flag (the deer horns,
    FFODeerHorns01 / FFOHornBase01); the wholesale shader copy carries it. The
    bake must STRIP FACE from non-Face parts (the CK does) or the engine runs
    facegen face/tint shading on the antlers' plain normal map -> black streaks.
    The actual Face head part must KEEP it."""
    head_rel = "meshes\\ffo\\test\\fhead.nif"
    horn_rel = "meshes\\ffo\\test\\fhorn.nif"
    _make_source(_loose(tmp_path, head_rel), [dict(
        name="TheHead", verts=[(0, 0, 0), (1, 0, 0), (1, 1, 0)], tris=[(0, 1, 2)],
        shader_type=4, flags=[ShaderFlags1FO4.FACE], s2b=_xf())])
    _make_source(_loose(tmp_path, horn_rel), [dict(
        name="TheHorn", verts=[(0, 0, 0), (1, 0, 0), (1, 1, 0)], tris=[(0, 1, 2)],
        shader_type=0, flags=[ShaderFlags1FO4.FACE], s2b=_xf())])  # FACE mis-set
    out = tmp_path / "outface.nif"
    headparts = [
        {"source_nif": head_rel, "hdpt_type": HDPT_FACE, "hdpt_edid": "TheHead"},
        {"source_nif": horn_rel, "hdpt_type": 0, "hdpt_edid": "TheHorn"},
    ]
    with AssetResolver(tmp_path, bsa_readers=[]) as resolver:
        assert build_facegen_nif("0000CAFE", "T.esp", headparts, resolver, out)
    shapes = {s.name: s for s in NifFile(str(out)).shapes}
    assert _f1(shapes["TheHead"]) & ShaderFlags1FO4.FACE, "FACE dropped from head"
    assert not (_f1(shapes["TheHorn"]) & ShaderFlags1FO4.FACE), \
        "FACE not stripped from non-face (horn) part"


def test_cloth_data_moved_to_hair_shape(tmp_path):
    """FO4 cloth-physics hair: the source nif carries a BSClothExtraData on its
    ROOT; the bake must re-attach it to the baked hair TriShape (matching CK).
    Without it the cloth bones have no simulation and the hair stretches to
    infinity/origin in-game (the Rosalind regression).

    The physics bone here is `SideTail_BN_*` (a twin-tail/pigtail rig, e.g. "Fairy
    Tails") whose name does NOT contain "Cloth" — the old name-based gate dropped
    its cloth data. Attachment is keyed on the shape being weighted to a bone the
    cloth block names (real FO4 cloth blobs embed their bone names)."""
    from ctypes import c_int, c_char, byref
    from pyn.niflydll import nifly

    def read_cloth(nif, target):
        out, nl, vl = [], c_int(), c_int()
        for i in range(100):
            if not nifly.getClothExtraDataLen(nif._handle, target, i,
                                              byref(nl), byref(vl)):
                break
            name = (c_char * (nl.value + 1))()
            val = (c_char * (vl.value + 1))()
            nifly.getClothExtraData(nif._handle, target, i, name, nl.value + 1,
                                    val, vl.value + 1)
            out.append((name.value.decode("utf-8"), val.raw))
        return out

    hair_rel = "meshes\\ffo\\test\\clothhair.nif"
    hair_path = _loose(tmp_path, hair_rel)
    nif = NifFile()
    nif.initialize("FO4", str(hair_path), root_type="NiNode", root_name="scene")
    phys_bone = "SideTail_BN_A_001"  # pigtail rig: no "Cloth" in the name
    nif.add_node(phys_bone, _xf(translation=(0.0, 0.0, 120.0)),
                 parent=nif.root)
    shp = nif.createShapeFromData(
        "ClothHair", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 2)],
        [(0.0, 0.0)] * 3, [(0.0, 0.0, 1.0)] * 3, use_type=_SITS)
    shp.skin()
    shp.add_bone(phys_bone)
    shp.set_skin_to_bone_xform(phys_bone, _xf())
    shp.setShapeWeights(phys_bone, [(i, 1.0) for i in range(3)])
    shp.shader.name = ""
    shp.save_shader_attributes()
    # real FO4 cloth blobs embed their bone names; the attach is keyed on that.
    cloth = b"NVCLOTHTESTDATA" + phys_bone.encode() + bytes(range(48))
    nif.cloth_data = [("Binary Data", cloth + b"\x00")]  # root, per FFO source
    nif.save()

    out = tmp_path / "outcloth.nif"
    headparts = [{"source_nif": hair_rel, "hdpt_type": 0}]
    with AssetResolver(tmp_path, bsa_readers=[]) as resolver:
        assert build_facegen_nif("0000BEEF", "T.esp", headparts, resolver, out)

    res = NifFile(str(out))
    shape = res.shapes[0]
    on_shape = read_cloth(res, shape._handle)
    on_root = read_cloth(res, None)
    assert len(on_shape) == 1, "cloth block not attached to the hair shape"
    assert on_shape[0][1].startswith(b"NVCLOTHTESTDATA"), "cloth payload wrong"
    assert len(on_root) == 0, "cloth wrongly left on the root (CK moves it)"


def test_shape_renamed_to_hdpt_edid(tmp_path):
    """CK names each facegen shape by its HDPT EditorID; we pass hdpt_edid
    through as the output shape name (source mesh name is replaced)."""
    head_rel = "meshes\\ffo\\test\\h.nif"
    head_path = _loose(tmp_path, head_rel)
    _make_source(head_path, [dict(
        name="SourceMeshName", verts=[(0, 0, 0), (1, 0, 0), (1, 1, 0)],
        tris=[(0, 1, 2)], shader_type=4, flags=[ShaderFlags1FO4.FACE], s2b=_xf())])
    out = tmp_path / "out.nif"
    headparts = [{"source_nif": head_rel, "hdpt_type": HDPT_FACE,
                  "hdpt_edid": "FFOMyHeadEDID"}]
    with AssetResolver(tmp_path, bsa_readers=[]) as resolver:
        assert build_facegen_nif("0000ABCD", "T.esp", headparts, resolver, out)
    names = [s.name for s in NifFile(str(out)).shapes]
    assert names == ["FFOMyHeadEDID"], names


def test_bone_name_canonicalized(built):
    """The FFO source miscases the head bone as 'HEAD'; FO4 binds skin to the
    skeleton by name, so the bake must canonicalize it to 'Head' (both the bone
    node and the shape's skin reference) or the head never binds in-game."""
    assert "Head" in built["nif"].nodes
    assert "HEAD" not in built["nif"].nodes
    assert built["shapes"]["FFOTestHead"].bone_names == ["Head"]


def test_shape_flags_copied(built):
    """NiAVObject flags carry from source (14); createShapeFromData defaults to
    0 and CK keeps 14."""
    for name, s in built["shapes"].items():
        assert s.flags == 14, f"{name} flags={s.flags}, expected 14"


def test_root_material_cleared(built):
    """Root Material must be NODEID_NONE on every shape — the wholesale shader
    copy carried a stale source string-index that resolved to garbage."""
    for name, s in built["shapes"].items():
        s.shader.properties
        assert s.shader._properties.rootMaterialNameID == 0xFFFFFFFF, (
            f"{name} kept a Root Material (id "
            f"{s.shader._properties.rootMaterialNameID})")


def test_bones_declared_before_facegen_node(built):
    """Linear parse order: a shape's bones must have lower block ids than the
    BSFaceGenNiNodeSkinned that parents the shapes."""
    nodes = built["nif"].nodes
    assert nodes["Head"].id < nodes["BSFaceGenNiNodeSkinned"].id


def test_face_diffuse_stamped(built):
    head = built["shapes"]["FFOTestHead"]
    diffuse = head.textures.get("Diffuse", "")
    assert diffuse.lower().endswith(
        "facecustomization\\testffo.esp\\00abcdef_d.dds"), diffuse


def test_non_face_shape_does_not_get_face_diffuse(built):
    hair = built["shapes"]["FFOTestHair"]
    diffuse = (hair.textures.get("Diffuse") or "")
    assert "facecustomization" not in diffuse.lower(), diffuse


def test_greyscale_hair_gets_palette_scale(built):
    hair = built["shapes"]["FFOTestHair"]
    hair.shader.properties
    assert hair.shader._properties.grayscaleToPaletteScale == pytest.approx(
        HAIR_PALETTE_SCALE)
