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
from pyn.structs import TransformBuf  # noqa: E402
from pyn.nifdefs import PynBufferTypes  # noqa: E402
from pyn.nifconstants import ShaderFlags1FO4  # noqa: E402

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


def _facegen_shader_type(material, current_type: int) -> int:
    """The facegen shader type CK assigns, derived from the BGSM material.

    CK derives the type from the material (baked inline), not the source nif's
    shader block — and FFO source nifs sometimes author it wrong. Two confirmed
    corrections:

    - `glowmap` material -> Glow. A glowmap hair left as Default makes the FO4
      renderer build a corrupt texture command buffer and access-violate in
      crowds (Diamond City). This is the load-bearing fix.
    - A Face/Skin-Tint type on a NON-skin-tinted material -> Default (e.g. FFO
      horns authored as Face_Tint), matching CK.

    `material` is None for shapes with no resolvable BGSM; then the type is left
    unchanged.
    """
    if material is None:
        return current_type
    if getattr(material, "glowmap", False):
        return _SHADER_GLOW
    if (not getattr(material, "skinTint", False)
            and current_type in (_SHADER_FACE_TINT, _SHADER_SKIN_TINT)):
        return _SHADER_DEFAULT
    return current_type


def _copy_shape(dst: NifFile, fg, src_shape, resolver, face_textures=None,
                texture_overrides=None, hair_palette_scale=None,
                verts=None, rename_to=None) -> None:
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
    new_shape.skin()
    if src_shape.has_global_to_skin:
        new_shape.set_global_to_skin(src_shape.global_to_skin)
    for bone in src_shape.bone_names:
        new_shape.add_bone(_canon_bone(bone))
    for bone in src_shape.bone_names:
        new_shape.set_skin_to_bone_xform(
            _canon_bone(bone), src_shape.get_shape_skin_to_bone(bone))
    for bone, vw in src_shape.bone_weights.items():
        new_shape.setShapeWeights(_canon_bone(bone), vw)

    # Shader: copy the ctypes property buffer wholesale. Resolve textures
    # BGSM-authoritatively and write them INLINE, then CLEAR the material name
    # — the CK does exactly this (a lingering material ref would override the
    # inline FaceCustomization textures we point the head at).
    src_sh = src_shape.shader
    src_sh.properties  # lazy-load
    new_sh = new_shape.shader
    if src_sh._properties is not None:
        new_sh._properties = src_sh._properties.copy()
    new_sh.name = ""  # drop the BGSM material ref (match CK bake)
    # Clear the Root Material too. The wholesale buffer copy carried the source
    # nif's rootMaterialNameID as a raw string-table index; in our output nif
    # that index resolves to a garbage string (e.g. 'Eyes'), and a bogus Root
    # Material makes the FO4 shader inherit the wrong base material. CK leaves it
    # empty.
    new_sh.properties.rootMaterialNameID = 0xFFFFFFFF  # NODEID_NONE
    # The wholesale property copy above carries every flag the source/BGSM
    # shader has; the ONLY flag we add is SKINNED. FFO's source headpart meshes
    # don't carry the SKINNED shader flag, but the baked facegeom shapes ARE
    # skinned — the CK sets it during bake. Without it the engine's
    # shadow/utility shader runs the NON-skinned vertex path over skinned vertex
    # data and crashes d3d11 (rdx=0 null-deref) the moment the head is actually
    # loaded (Addictol bFacegen). Every facegen head part is skinned, so set it
    # unconditionally — there's no shape here where that would be wrong.
    new_sh.properties.shaderflags1_set(ShaderFlags1FO4.SKINNED)
    new_sh._properties.Shader_Type = _facegen_shader_type(
        src_sh.materials, new_sh._properties.Shader_Type)
    is_greyscale = bool(getattr(src_sh, "flag_greyscale_color", False))
    if is_greyscale and hair_palette_scale is not None:
        new_sh.properties.grayscaleToPaletteScale = float(hair_palette_scale)
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
                      aux_textures: bool = False) -> bool:
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
                    xf.translation = src_xf.translation
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
                    rename_to=hp.get("hdpt_edid"))

    dst.save()
    log.debug("wrote facegeom %s (%d bytes)", dst_path,
              os.path.getsize(dst_path))
    return True
