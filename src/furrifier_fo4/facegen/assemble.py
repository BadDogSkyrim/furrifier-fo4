"""Assemble an FO4 facegeom nif from an NPC's head-part source nifs.

Stacks each head part's shape under a single BSFaceGenNiNodeSkinned, matching
the vanilla CK bake (verified against Fallout4.esm\\…\\000A0E33.NIF):

  - root: NiNode, flags 0x400E
  - skin-cluster bones (Chest/Neck/HEAD group) as NiNode stubs, identity
    rotation + bind-pose translation, declared BEFORE the facegen node
  - shapes: BSSubIndexTriShape + BSSkin::Instance, skin data (bones,
    skin-to-bone, weights, segments) copied VERBATIM from the source head-part
    nifs — the facebone skin-to-bone rotations must survive untouched or the
    face warps in-game and in the 3D preview
  - the Face head part's shader textures point at the baked FaceCustomization
    set (`_d`/`_msn`/`_s`)

No morph baking: furrify clears the NPC's morphs, so the head is race-default
geometry. The FFO source head parts are already skinned BSSubIndexTriShapes, so
assembly is a faithful copy — no geometry is synthesized.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from .._pyn import ensure_dev_path
from .assets import AssetResolver
from .basehead import (resolve_shape_textures, resolve_shape_alpha,
                       ensure_materials_cached)
from .headparts_resolve import HDPT_FACE
from .tri_morph import morphed_verts
from .facebones import (load_facebones_shape, facebone_displacements,
                       load_facebone_skeleton, SKELETON_FACEBONES)

ensure_dev_path()
from pyn.pynifly import NifFile  # noqa: E402
from pyn.niflydll import nifly  # noqa: E402
from pyn.structs import TransformBuf  # noqa: E402
from pyn.nifdefs import PynBufferTypes  # noqa: E402
from pyn.nifconstants import ShaderFlags1FO4, ShaderFlags2FO4  # noqa: E402

log = logging.getLogger(__name__)

_GAME = "FO4"
_ROOT_TYPE = "BSFadeNode"
_ROOT_FLAGS = 0x2000400E  # matches CK FO4 facegeom roots (BSFadeNode)
_FACECUST = "textures\\Actors\\Character\\FaceCustomization"
# The single shared hair-color gradient LUT. FO4 hair shapes use
# "greyscale-to-palette-color": the grayscale diffuse is mapped through this
# gradient, with grayscaleToPaletteScale picking the row (the hair color). The
# CK bakes this into the Greyscale texture slot; our BGSM-first texture resolve
# drops it, so we restore it explicitly for any greyscale-color shape.
_HAIRCOLOR_LGRAD = "textures\\Actors\\Character\\Hair\\HairColor_LGrad_d.dds"


def _identity() -> TransformBuf:
    xf = TransformBuf()
    xf.set_identity()
    return xf


# FO4 head-region skeleton bone names, canonical case. The engine binds skin to
# the skeleton BY NAME, and some FFO source head nifs miscase the main head bone
# as "HEAD" — a miscased bone never binds, so the head never attaches/positions
# correctly in-game (renders dark/wrong, fine in NifSkope). CK canonicalizes
# against the skeleton; we do the same. Unknown bones pass through unchanged.
_CANON_BONES = {
    n.lower(): n for n in (
        "Head", "Neck", "Neck1_skin", "Neck_skin", "Neck_Low_skin",
        "Head_skin", "Chest", "Chest_skin", "Chest_Rear_Skin",
        "LArm_Collarbone_skin", "RArm_Collarbone_skin",
    )
}


def _canon_bone(name: str) -> str:
    return _CANON_BONES.get(name.lower(), name)


# BSLightingShaderProperty shader types we care about for facegen.
_SHADER_DEFAULT = 0
_SHADER_GLOW = 2
_SHADER_FACE_TINT = 4
_SHADER_SKIN_TINT = 5


def _apply_material_shading(props, mat) -> None:
    """Copy specular colour/strength + backlight power from the BGSM material
    onto the shader properties (CK bakes these from the material; FFO leaves the
    nif's inline block zeroed). getattr-guarded: an effect (BGEM) material — seen
    on some NPCs' hair — lacks these BGSM fields, so skip them rather than throw,
    which would fail the whole bake for that NPC. A shape with no specular keeps
    its copied value (0)."""
    sc = getattr(mat, "specularColor", None)
    if sc is not None:
        for i in range(3):
            props.Spec_Color[i] = sc[i]
    sm = getattr(mat, "specularMult", None)
    if sm is not None:
        props.Spec_Str = sm
    bp = getattr(mat, "backlightPower", None)
    if bp is not None:
        props.backlightPower = bp


def _facegen_shader_type(material, current_type: int) -> int:
    """The facegen shader type CK assigns, derived from the BGSM material.

    CK derives the type from the material's flags (baked inline), not the source
    nif's shader block — and FFO source nifs sometimes author it wrong. Rules, in
    priority order:

    - `glowmap` -> Glow. A glowmap hair left as Default makes the FO4 renderer
      build a corrupt texture command buffer and access-violate in crowds
      (Diamond City). This is the load-bearing fix.
    - `facegen` -> Face Tint. The face/head BGSM sets the `facegen` flag (NOT
      `skinTint`) — that flag *is* "Face Tint", so it drives the type even when
      the source nif authored Default. Without it the furry head baked as Default
      (no skin/subsurface shading) and rendered wrong in-game (FoxFemaleHead).
    - A Face/Skin-Tint source type on a material that's neither facegen nor
      skinTint -> Default (e.g. FFO horns mis-authored as Face_Tint).

    `material` is None for shapes with no resolvable BGSM; then the type is left
    unchanged.
    """
    if material is None:
        return current_type
    if getattr(material, "glowmap", False):
        return _SHADER_GLOW
    if getattr(material, "facegen", False):
        return _SHADER_FACE_TINT
    if (not getattr(material, "skinTint", False)
            and current_type in (_SHADER_FACE_TINT, _SHADER_SKIN_TINT)):
        return _SHADER_DEFAULT
    return current_type


def _copy_shape(dst: NifFile, fg, src_shape, resolver, face_textures=None,
                texture_overrides=None, hair_palette_scale=None,
                verts=None, rename_to=None, bone_xforms=None,
                skin_tone=None) -> None:
    """Copy one source head-part shape into `dst` under `fg`, verbatim.

    `verts` overrides the source vertices (used to bake the head's chargen face
    morphs into the Face shape); None copies them unchanged.

    `rename_to` names the output shape (the part's HDPT EditorID); CK renames
    each facegen shape to its HDPT EDID, and the engine appears to match
    shape -> HDPT -> tri by name. None keeps the source shape name.

    For a greyscale-to-palette (hair) shape, restore the shared hair-color
    gradient in the Greyscale slot and set grayscaleToPaletteScale to
    `hair_palette_scale` (the NPC's hair-color position), so the hair takes the
    NPC's colour instead of rendering as the raw grayscale.
    """
    uvs = list(src_shape.uvs) if src_shape.uvs else []
    normals = list(src_shape.normals) if src_shape.normals else None
    new_shape = dst.createShapeFromData(
        rename_to or src_shape.name,
        verts if verts is not None else list(src_shape.verts),
        list(src_shape.tris), uvs,
        normals, use_type=PynBufferTypes.BSSubIndexTriShapeBufType, parent=fg)
    # Identity local transform, matching CK. CK positions the head via the skin
    # (identity bone rotation + skin-to-bone), not a baked shape transform.
    # Carrying the source's +120 shape transform diverged from CK's frame and
    # broke the face in-game — tangent-space _msn normals are frame-sensitive.
    new_shape.transform = _identity()
    # NiAVObject flags: createShapeFromData defaults to 0; the source (and CK)
    # carry 14. Copy them so the shape isn't left with a wrong flag set.
    new_shape.flags = src_shape.flags
    if src_shape.colors:
        new_shape.set_colors(list(src_shape.colors))

    # Skin: add all bones first (add_bone resets skin data), then s2b, weights.
    # Bone names are canonicalized to the skeleton's casing (e.g. "HEAD"->"Head")
    # so the skin actually binds in-game.
    #
    # Give each bone its NODE bind transform from `bone_xforms` (built once in
    # build_facegen_nif: identity rotation + height-scaled source translation,
    # matching CK). add_bone defaults to IDENTITY, which collapses any bone the
    # actor skeleton doesn't supply — i.e. nif-local cloth-physics hair bones —
    # since `identity @ skin_to_bone(inverse-bind)` yanks their verts to the
    # origin / out to infinity. See the bone_xforms comment for the height scale.
    new_shape.skin()
    if src_shape.has_global_to_skin:
        new_shape.set_global_to_skin(src_shape.global_to_skin)
    for bone in src_shape.bone_names:
        canon = _canon_bone(bone)
        bind = bone_xforms.get(canon) if bone_xforms else None
        new_shape.add_bone(canon, bind)
    for bone in src_shape.bone_names:
        new_shape.set_skin_to_bone_xform(
            _canon_bone(bone), src_shape.get_shape_skin_to_bone(bone))
    for bone, vw in src_shape.bone_weights.items():
        new_shape.setShapeWeights(_canon_bone(bone), vw)

    # --- Shader: rebuild from the source block + BGSM material, keyed on the
    # facegen shader TYPE (the plan's "derive by rule, not copy-and-patch").
    # We copy the source's inline shader buffer, apply the UNIVERSAL facegen
    # fixups (drop refs, force SKINNED, bake material shading, TRANSFORM_CHANGED),
    # then dispatch on the FINAL shader type for the type-specific handling. ---
    src_sh = src_shape.shader
    src_sh.properties  # lazy-load
    new_sh = new_shape.shader
    if src_sh._properties is not None:
        new_sh._properties = src_sh._properties.copy()
    p = new_sh._properties

    # Universal:
    # - name "": drop the BGSM material ref (CK inlines the textures instead; a
    #   lingering ref would override the inline FaceCustomization textures).
    # - rootMaterialNameID NONE: the copied index resolves to a garbage string in
    #   our output; NONE => no RootMaterialPath (CK's string-index-0 means the
    #   same empty — the golden test compares the resolved path, not the index).
    # - SKINNED: FFO source heads omit it, but baked facegeom shapes ARE skinned;
    #   without it the engine's shadow/utility shader runs the non-skinned vertex
    #   path over skinned data and access-violates d3d11 (Addictol bFacegen CTD).
    # - specular + backlight from the BGSM material: CK bakes these from the
    #   material; FFO leaves the nif's inline block zeroed (no spec/backlight).
    # - TRANSFORM_CHANGED (F2 0x80): CK sets it on every baked shape.
    new_sh.name = ""
    p.rootMaterialNameID = 0xFFFFFFFF  # NODEID_NONE
    new_sh.properties.shaderflags1_set(ShaderFlags1FO4.SKINNED)
    if src_sh.materials is not None:
        _apply_material_shading(p, src_sh.materials)
    p.Shader_Flags_2 |= int(ShaderFlags2FO4.TRANSFORM_CHANGED)

    # Final shader type from the material (fixes FFO's mis-authored source types).
    shader_type = _facegen_shader_type(src_sh.materials, p.Shader_Type)
    p.Shader_Type = shader_type

    # Type-specific handling:
    #   Face Tint  -> FACE flag (facegen detail-map / tint shading). The face is
    #                 the only part that gets it; FFO mis-authors it on the deer
    #                 horns, where face shading on a plain normal map = black
    #                 streaks, so every non-Face-Tint shape must have it cleared.
    #   Skin Tint  -> skinTintColor from the NPC's skin tone (QNAM); CK bakes the
    #                 same RGBA (the furry horn base — white otherwise).
    #   Glow / Default / Environment Map -> no extra flags (the type + the source
    #                 block already carry what they need; eyes keep the modder's
    #                 env-map setup verbatim).
    if shader_type == _SHADER_FACE_TINT:
        new_sh.properties.shaderflags1_set(ShaderFlags1FO4.FACE)
    else:
        new_sh.properties.shaderflags1_clear(ShaderFlags1FO4.FACE)
    if shader_type == _SHADER_SKIN_TINT and skin_tone:
        for i in range(3):
            p.skinTintColor[i] = skin_tone[i]
        p.Skin_Tint_Alpha = skin_tone[3]

    # Greyscale-to-palette hair colour (the NPC's hair-colour position). Used
    # again below to restore the hair gradient in the Greyscale texture slot.
    is_greyscale = bool(getattr(src_sh, "flag_greyscale_color", False))
    if is_greyscale and hair_palette_scale is not None:
        p.grayscaleToPaletteScale = float(hair_palette_scale)
    new_shape.save_shader_attributes()
    textures = dict(resolve_shape_textures(src_shape, resolver))
    if texture_overrides:
        textures.update(texture_overrides)
    if face_textures:
        textures.update(face_textures)
    grey = textures.pop("Greyscale", None)
    for slot, path in textures.items():
        if path:
            new_shape.set_texture(slot, path)
    # Restore the hair-color gradient the BGSM resolve dropped (the CK always
    # bakes it for greyscale-color shapes). FO4 reads the gradient from texture
    # slot index 4, which PyNifly surfaces as "Greyscale" when the greyscale-
    # color flag is set. set_texture has no 'Greyscale' write case for a
    # lighting shader, and its write indices run one BELOW the read indices
    # (Diffuse writes 0/reads 1, Specular writes 7/reads 8) — so the key that
    # writes the slot read back as Greyscale (index 4) is 'HeightMap' (writes
    # 3). Hair sets neither PARALLAX nor env-map flags, so the read is
    # unambiguous. (PyNifly gap: add a 'Greyscale' write case for FO4 lighting.)
    if is_greyscale:
        new_shape.set_texture("HeightMap", grey or _HAIRCOLOR_LGRAD)
    new_shape.save_shader_attributes()

    # Alpha property: prefer the source nif's, else synthesize from the BGSM.
    # Hair cards use alpha test/blend; some source nifs carry a NiAlphaProperty,
    # but many FFO hairs keep the cutout ONLY in the material (the nif has none)
    # — and we clear the material ref, so without this they bake fully opaque
    # in-game and in the preview (e.g. FringeFlip). The CK derives it from the
    # BGSM; so do we.
    if src_shape.has_alpha_property:
        src_ap = src_shape.alpha_property.properties
        alpha = (src_ap.flags, src_ap.threshold)
    else:
        alpha = resolve_shape_alpha(src_shape, resolver)
    if alpha is not None:
        new_shape.has_alpha_property = True
        new_ap = new_shape.alpha_property.properties
        new_ap.flags, new_ap.threshold = alpha
        new_shape.save_alpha_property()

    # Segments (FO4): copy the source's verbatim.
    if src_shape.partitions:
        try:
            new_shape.set_partitions(src_shape.partitions,
                                     src_shape.partition_tris)
        except Exception as exc:
            log.debug("segment copy failed for %s: %s", src_shape.name, exc)

    # Cloth physics (FO4 hair with Hair_*_Cloth* / Ponytail_*_Cloth* bones):
    # the source nif carries a BSClothExtraData on its ROOT. The CK moves that
    # block VERBATIM onto the hair TriShape in the facegeom (byte-identical even
    # though the bones are height-scaled — verified md5-equal vs source). Without
    # it the cloth bones have no simulation and the hair stretches to infinity /
    # the origin in-game. PyNifly's `cloth_data` property only reads/writes the
    # ROOT, so attach to the shape by calling the DLL with the shape handle as
    # the target (None would be the root). `len(data) - 1` matches PyNifly's own
    # setter convention (the read buffer carries a trailing null).
    if any("Cloth" in b for b in src_shape.bone_names):
        for name, data in src_shape.file.cloth_data:
            nifly.setClothExtraData(dst._handle, new_shape._handle,
                                    name.encode("utf-8"), data, len(data) - 1)


def _apply_facebones(base_verts: list, facebones_rel: str, deltas: dict,
                     resolver) -> list:
    """Add the facebone (region) LBS displacement to `base_verts`, using the
    facebones-skinned head nif (`facebones_rel`) for the skin weights + bone
    transforms. Returns `base_verts` unchanged if the nif is missing or its vert
    count doesn't match (a guard — no corruption)."""
    fb_path = resolver.resolve(facebones_rel)
    if fb_path is None:
        log.debug("facebones nif missing: %s", facebones_rel)
        return base_verts
    shape = load_facebones_shape(str(fb_path))
    if shape is None or len(shape.verts) != len(base_verts):
        if shape is not None:
            # Per-part now: a part whose facebones nif vert count doesn't match
            # its shape just skips region morphs (kept as DEBUG to avoid spam).
            log.debug("facebones nif %s has %d verts, shape has %d; region "
                      "morphs skipped", facebones_rel, len(shape.verts),
                      len(base_verts))
        return base_verts
    # The facebone skeleton supplies the bone hierarchy so a region that drives
    # a control bone (e.g. skin_bone_L_Ear) deforms the head's descendant skin
    # bone (skin_bone_L_EarTop). Missing -> direct-skin bones still work.
    skel_path = resolver.resolve("\\".join(SKELETON_FACEBONES))
    skeleton = load_facebone_skeleton(str(skel_path)) if skel_path else None
    disp = facebone_displacements(shape, deltas, skeleton=skeleton)
    return [(v[0] + d[0], v[1] + d[1], v[2] + d[2])
            for v, d in zip(base_verts, disp)]


def build_facegen_nif(form_id: str, base_plugin: str, headparts: list,
                      resolver: AssetResolver, dst_path: Path,
                      hair_palette_scale: Optional[float] = None,
                      base_normal: Optional[str] = None,
                      base_specular: Optional[str] = None,
                      aux_textures: bool = False,
                      bone_scale: float = 1.0,
                      skin_tone=None) -> bool:
    """Assemble and write the facegeom nif for one NPC. Returns True on success.

    `headparts` come from `resolve_headparts`. The Face part's Diffuse is always
    the per-NPC baked FaceCustomization `_d` under `base_plugin`.

    The Normal/Specular are RACE-CONSTANT (FFO tints only the diffuse), so by
    default the head points at the SHARED base-head maps (`base_normal` /
    `base_specular`, textures-relative) instead of a per-NPC `_msn`/`_s` — the
    per-NPC duplication multiplied face-texture VRAM ~7x and crashed the
    renderer in crowds. A missing base map falls through to the head shape's own
    resolved texture. `aux_textures=True` points the head back at the per-NPC
    `_msn`/`_s` set (for a future pass that bakes layers onto the normal, e.g.
    scars); the caller must also write those files (`bake_aux`).

    `hair_palette_scale` (the NPC's HCLF hair-color position, 0..1) colours the
    hair shape via greyscale-to-palette; None leaves the source default.
    """
    face_textures = {
        "Diffuse": f"{_FACECUST}\\{base_plugin}\\{form_id}_d.dds",
    }
    if aux_textures:
        face_textures["Normal"] = f"{_FACECUST}\\{base_plugin}\\{form_id}_msn.dds"
        face_textures["Specular"] = f"{_FACECUST}\\{base_plugin}\\{form_id}_s.dds"
    else:
        # Shared race maps; omit (fall through to the source head's own slot)
        # when the base head defines no normal/specular.
        if base_normal:
            face_textures["Normal"] = base_normal
        if base_specular:
            face_textures["Specular"] = base_specular

    # Open each source nif once; collect shapes + bone bind-pose TRANSLATIONS.
    # Match CK: bone-node rotation is IDENTITY (translation only). We previously
    # baked the source's full bind-pose rotation in (to render upright without an
    # external skeleton), but CK zeroes it and positions via the skin — and the
    # divergent frame broke the face in-game, because FO4 face _msn maps are
    # tangent-space and the tangent basis depends on this frame. With the shape
    # transform now identity too, the head positions through the skin like CK's.
    sources = []
    bone_xforms: dict = {}
    for hp in headparts:
        src_path = resolver.resolve(hp["source_nif"])
        if src_path is None:
            log.debug("head part nif missing: %s", hp["source_nif"])
            continue
        # Cache referenced BGSMs, then point PyNifly's material search at the
        # cache so opening the nif doesn't spam "Could not find materials file"
        # (and the shader reads come from the BGSM, not the nif's inline block).
        ensure_materials_cached(resolver, hp["source_nif"], src_path)
        src_nif = NifFile(str(src_path), materialsRoot=resolver.cache_root)
        for src_shape in src_nif.shapes:
            sources.append((hp, src_shape))
            for bone in src_shape.bone_names:
                canon = _canon_bone(bone)
                if canon not in bone_xforms and bone in src_nif.nodes:
                    src_xf = src_nif.nodes[bone].transform
                    xf = TransformBuf()
                    xf.set_identity()  # identity rotation, matching CK
                    # Scale bone positions by the race's per-sex height: the CK
                    # bakes the facegen skeleton at this scale. Skeleton bones get
                    # rebound to the (height-scaled) actor skeleton in-game so
                    # their scale is moot — but NIF-LOCAL bones not in the skeleton
                    # (cloth-physics hair: Hair_*_Cloth* / Ponytail_*_Cloth*) are
                    # NOT rebound, so at a race height != 1.0 they'd stay unscaled
                    # and the cloth-weighted hair verts stretch off the head.
                    # Verified: female Fox/Lykaios height = 0.98.
                    t = src_xf.translation
                    xf.translation = (t[0] * bone_scale, t[1] * bone_scale,
                                      t[2] * bone_scale)
                    bone_xforms[canon] = xf

    if not sources:
        log.warning("no head-part shapes resolved for %s", form_id)
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    dst = NifFile()
    dst.initialize(_GAME, str(dst_path), root_type=_ROOT_TYPE,
                   root_name="")  # CK leaves the BSFadeNode root name empty
    dst.root.name = ""
    dst.root.flags = _ROOT_FLAGS

    # Bones first (so the shapes that reference them parse), then the facegen
    # node. Bind-pose transform (rotation + translation) from the source nifs.
    for bone in sorted(bone_xforms):
        dst.add_node(bone, bone_xforms[bone], parent=dst.root)
    fg = dst.add_node("BSFaceGenNiNodeSkinned", _identity(), parent=dst.root)

    for hp, src_shape in sources:
        is_face = hp.get("hdpt_type") == HDPT_FACE
        ft = face_textures if is_face else None
        # Bake face morphs into EVERY part's verts (not just the Face): chargen
        # tri shape keys (phase 2) then facebone region deformation (phase 3),
        # each through this part's own tri / facebones nif, so a separate part
        # like the deer mouth deforms with the head instead of detaching from
        # the morphed snout. Parts without a matching tri/facebones nif fall
        # through the inner guards unchanged.
        verts = None
        morphs = hp.get("morphs")
        fb_deltas = hp.get("facebone_deltas")
        if morphs or fb_deltas:
            verts = list(src_shape.verts)
            if morphs and hp.get("chargen_tri"):
                tri_path = resolver.resolve(hp["chargen_tri"])
                if tri_path is not None:
                    verts = morphed_verts(verts, str(tri_path), morphs)
            if fb_deltas and hp.get("facebones_nif"):
                verts = _apply_facebones(verts, hp["facebones_nif"], fb_deltas,
                                         resolver)
        _copy_shape(dst, fg, src_shape, resolver, face_textures=ft,
                    texture_overrides=hp.get("textures"),
                    hair_palette_scale=hair_palette_scale, verts=verts,
                    rename_to=hp.get("hdpt_edid"), bone_xforms=bone_xforms,
                    skin_tone=skin_tone)

    dst.save()
    log.debug("wrote facegeom %s (%d bytes)", dst_path,
              os.path.getsize(dst_path))
    return True
