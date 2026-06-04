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
from .basehead import resolve_shape_textures, resolve_shape_alpha
from .headparts_resolve import HDPT_FACE

ensure_dev_path()
from pyn.pynifly import NifFile  # noqa: E402
from pyn.structs import TransformBuf  # noqa: E402
from pyn.nifdefs import PynBufferTypes  # noqa: E402

log = logging.getLogger(__name__)

_GAME = "FO4"
_ROOT_TYPE = "NiNode"
_ROOT_FLAGS = 0x400E  # 16398, matches vanilla FO4 facegeom roots.
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


def _copy_shape(dst: NifFile, fg, src_shape, resolver, face_textures=None,
                texture_overrides=None, hair_palette_scale=None) -> None:
    """Copy one source head-part shape into `dst` under `fg`, verbatim.

    For a greyscale-to-palette (hair) shape, restore the shared hair-color
    gradient in the Greyscale slot and set grayscaleToPaletteScale to
    `hair_palette_scale` (the NPC's hair-color position), so the hair takes the
    NPC's colour instead of rendering as the raw grayscale.
    """
    uvs = list(src_shape.uvs) if src_shape.uvs else []
    normals = list(src_shape.normals) if src_shape.normals else None
    new_shape = dst.createShapeFromData(
        src_shape.name, list(src_shape.verts), list(src_shape.tris), uvs,
        normals, use_type=PynBufferTypes.BSSubIndexTriShapeBufType, parent=fg)
    new_shape.transform = src_shape.transform
    if src_shape.colors:
        new_shape.set_colors(list(src_shape.colors))

    # Skin: add all bones first (add_bone resets skin data), then s2b, weights.
    new_shape.skin()
    if src_shape.has_global_to_skin:
        new_shape.set_global_to_skin(src_shape.global_to_skin)
    for bone in src_shape.bone_names:
        new_shape.add_bone(bone)
    for bone in src_shape.bone_names:
        new_shape.set_skin_to_bone_xform(
            bone, src_shape.get_shape_skin_to_bone(bone))
    for bone, vw in src_shape.bone_weights.items():
        new_shape.setShapeWeights(bone, vw)

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


def build_facegen_nif(form_id: str, base_plugin: str, headparts: list,
                      resolver: AssetResolver, dst_path: Path,
                      hair_palette_scale: Optional[float] = None) -> bool:
    """Assemble and write the facegeom nif for one NPC. Returns True on success.

    `headparts` come from `resolve_headparts`. The Face part's textures are
    pointed at the baked FaceCustomization set under `base_plugin`.
    `hair_palette_scale` (the NPC's HCLF hair-color position, 0..1) colours the
    hair shape via greyscale-to-palette; None leaves the source default.
    """
    face_textures = {
        "Diffuse": f"{_FACECUST}\\{base_plugin}\\{form_id}_d.dds",
        "Normal": f"{_FACECUST}\\{base_plugin}\\{form_id}_msn.dds",
        "Specular": f"{_FACECUST}\\{base_plugin}\\{form_id}_s.dds",
    }

    # Open each source nif once; collect shapes + bone bind-pose transforms.
    # We keep the source bone node's FULL transform (rotation + translation),
    # not just the translation: the source headpart nifs carry the real
    # skeleton bind-pose rotation (≈ inverse of the shape's skin-to-bone), and
    # baking it in makes the facegeom self-consistent — it renders upright on
    # its own (3D preview, NifSkope) instead of relying on an external skeleton
    # the way the CK's identity-rotation output does. In-game is unaffected (the
    # engine binds skin to the skeleton by bone name regardless).
    sources = []
    bone_xforms: dict = {}
    for hp in headparts:
        src_path = resolver.resolve(hp["source_nif"])
        if src_path is None:
            log.debug("head part nif missing: %s", hp["source_nif"])
            continue
        src_nif = NifFile(str(src_path))
        for src_shape in src_nif.shapes:
            sources.append((hp, src_shape))
            for bone in src_shape.bone_names:
                if bone not in bone_xforms and bone in src_nif.nodes:
                    src_xf = src_nif.nodes[bone].transform
                    xf = TransformBuf()
                    xf.set_identity()
                    xf.translation = src_xf.translation
                    xf.rotation = src_xf.rotation
                    bone_xforms[bone] = xf

    if not sources:
        log.warning("no head-part shapes resolved for %s", form_id)
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    dst = NifFile()
    dst.initialize(_GAME, str(dst_path), root_type=_ROOT_TYPE,
                   root_name=f"{form_id}.nif")
    dst.root.flags = _ROOT_FLAGS

    # Bones first (so the shapes that reference them parse), then the facegen
    # node. Bind-pose transform (rotation + translation) from the source nifs.
    for bone in sorted(bone_xforms):
        dst.add_node(bone, bone_xforms[bone], parent=dst.root)
    fg = dst.add_node("BSFaceGenNiNodeSkinned", _identity(), parent=dst.root)

    for hp, src_shape in sources:
        ft = face_textures if hp.get("hdpt_type") == HDPT_FACE else None
        _copy_shape(dst, fg, src_shape, resolver, face_textures=ft,
                    texture_overrides=hp.get("textures"),
                    hair_palette_scale=hair_palette_scale)

    dst.save()
    log.debug("wrote facegeom %s (%d bytes)", dst_path,
              os.path.getsize(dst_path))
    return True
