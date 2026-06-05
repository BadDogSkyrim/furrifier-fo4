"""Resolve a furry race's base head textures (the diffuse tints composite onto).

For each (race, sex) the Face-type HDPT's model nif carries the head shape.
In FO4 the **material file (.BGSM/.BGEM) is the authoritative source** for a
shape's texture paths; the nif's inline BSShaderTextureSet is only a fallback
when the material file can't be found. (FFO head nifs sometimes ship a
placeholder inline diffuse like `textures\\g` and put the real paths in the
BGSM.) The shader's name is the material path; we resolve it through our own
AssetResolver — which finds BGSMs in BA2 archives that PyNifly's own search
misses — and parse it. The FaceCustomization diffuse is the base diffuse with
tints baked in; normal/specular pass through.

Results are cached per (race, sex).
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import Sex
from .._pyn import ensure_dev_path

log = logging.getLogger(__name__)


def _norm_tex(path: str) -> str:
    """Normalize a textures-relative path to a Data-relative one."""
    p = path.replace("/", "\\")
    if not p.lower().startswith("textures\\"):
        p = "textures\\" + p.lstrip("\\")
    return p


def _norm_mat(path: str) -> str:
    """Normalize a material path to a Data-relative `materials\\...` one."""
    p = path.replace("/", "\\")
    if not p.lower().startswith("materials\\"):
        p = "materials\\" + p.lstrip("\\")
    return p


# nif relpaths whose referenced materials we've already extracted into the cache
# (process-local; each parallel bake worker has its own resolver + cache).
_MATERIALS_CACHED: set = set()


def ensure_materials_cached(resolver, nif_relpath: str, nif_path) -> None:
    """Extract a (BA2-sourced) nif's referenced BGSM/BGEM materials into the
    SAME temp cache as the nif, so PyNifly resolves them instead of spamming
    "Could not find materials file".

    PyNifly's `find_referenced_file` looks for a sibling `materials\\` tree next
    to the nif (root = the path up to `meshes\\`). A nif extracted from a BA2
    lives at `<cache>/meshes/...`, but its BGSM may sit in a *different* BA2 that
    PyNifly can't read, so the lookup fails on every shader/texture access. We
    pre-resolve each shape's material via our own AssetResolver (BA2-aware),
    which writes it to `<cache>/materials/...` — exactly where PyNifly looks.

    One-time per nif. The brief scan open silences pynifly's logger because the
    materials genuinely aren't cached yet *at scan time*; after this the real
    bake open of the nif finds them and is warning-free."""
    key = nif_relpath.replace("/", "\\").lower()
    if key in _MATERIALS_CACHED:
        return
    _MATERIALS_CACHED.add(key)
    ensure_dev_path()
    from pyn.pynifly import NifFile

    pyn_log = logging.getLogger("pynifly")
    prev = pyn_log.level
    pyn_log.setLevel(logging.ERROR)
    try:
        nif = NifFile(str(nif_path))
        for shape in nif.shapes:
            matname = getattr(shape.shader, "name", None)
            if matname:
                resolver.resolve(_norm_mat(matname))
    except Exception as exc:
        log.debug("material prefetch failed for %s: %s", nif_relpath, exc)
    finally:
        pyn_log.setLevel(prev)


def resolve_shape_textures(shape, resolver) -> dict:
    """Authoritative texture slots for a PyNifly shape: the BGSM/BGEM material
    if resolvable (FO4's source of truth), else the nif's inline set. Slot
    names are PyNifly shader slots; paths are Data-relative.

    The CK bakes these resolved paths inline into the facegeom and drops the
    material reference, so assemble.py writes them inline + clears the material
    name (a lingering material ref would override the inline FaceCustomization).
    """
    matname = getattr(shape.shader, "name", None)
    if matname:
        mat_path = resolver.resolve(_norm_mat(matname))
        if mat_path is not None:
            from pyn.bgsmaterial import MaterialFile

            mat = MaterialFile.Open(str(mat_path))
            if mat is not None and mat.textures:
                out = {}
                for slot, p in mat.textures.items():
                    if slot == "RootMaterialPath" or not p:
                        continue
                    out[slot] = _norm_tex(p)
                if out:
                    return out
    return {slot: _norm_tex(p) for slot, p in shape.textures.items() if p}


def resolve_shape_alpha(shape, resolver):
    """If the shape's BGSM/BGEM material enables alpha test or blend, return
    `(flags, threshold)` for a synthesized NiAlphaProperty; else None.

    FO4 hair/eyebrow cards keep their cutout in the MATERIAL, not the nif (e.g.
    FFOHairFringeFlip.BGSM: alphatest=True, ref=90, no nif NiAlphaProperty), so
    a bake that only copies the nif's alpha leaves them fully opaque — in-game
    and in the preview. The CK derives this from the BGSM; so do we.

    NiAlphaProperty flags: bit0 blend-enable, bits1-4 src-blend, bits5-8
    dst-blend, bit9 test-enable, bits10-12 test-func. The BGSM stores the blend
    enable + src/dst funcs (alphblend0/1/2) and the test enable + ref
    (alphatest / alphatestref); cutouts use test function GREATER (4). For a
    typical hair this yields flags 0x12EC (blend off, src 6, dst 7, test on).
    """
    matname = getattr(shape.shader, "name", None)
    if not matname:
        return None
    mat_path = resolver.resolve(_norm_mat(matname))
    if mat_path is None:
        return None
    from pyn.bgsmaterial import MaterialFile

    mat = MaterialFile.Open(str(mat_path))
    if mat is None:
        return None
    blend_on = bool(getattr(mat, "alphblend0", 0))
    test_on = bool(getattr(mat, "alphatest", False))
    if not blend_on and not test_on:
        return None
    src = int(getattr(mat, "alphblend1", 6)) & 0xF
    dst = int(getattr(mat, "alphblend2", 7)) & 0xF
    flags = (src << 1) | (dst << 5)
    if blend_on:
        flags |= 0x1
    if test_on:
        flags |= 0x200 | (4 << 10)  # test enable + GREATER
    threshold = int(getattr(mat, "alphatestref", 0)) & 0xFF
    return flags, threshold


class BaseHeadTextures:
    """(race, sex) -> {diffuse, normal, specular} Data-relative paths."""

    def __init__(self, headpart_pools, resolver):
        self.pools = headpart_pools
        self.resolver = resolver
        self._cache: dict = {}

    def _material_textures(self, shape) -> Optional[dict]:
        """Texture paths from the shape's BGSM/BGEM material, or None if the
        material file can't be resolved/parsed."""
        matname = getattr(shape.shader, "name", None)
        if not matname:
            return None
        mat_path = self.resolver.resolve(_norm_mat(matname))
        if mat_path is None:
            log.debug("material file not found, will fall back to nif: %s", matname)
            return None
        from pyn.bgsmaterial import MaterialFile

        mat = MaterialFile.Open(str(mat_path))
        if mat is None or not mat.textures.get("Diffuse"):
            return None
        return mat.textures

    def _read_nif_textures(self, model_rel: str) -> Optional[dict]:
        ensure_dev_path()
        from pyn.pynifly import NifFile

        nif_rel = "meshes\\" + model_rel
        nif_path = self.resolver.resolve(nif_rel)
        if nif_path is None:
            log.warning("base head nif not found: %s", model_rel)
            return None
        ensure_materials_cached(self.resolver, nif_rel, nif_path)
        nif = NifFile(str(nif_path), materialsRoot=self.resolver.cache_root)
        # The head is the highest-poly shape (the only Face shape in the nif).
        shape = max(nif.shapes, key=lambda s: len(s.verts), default=None)
        if shape is None:
            return None

        # Material file first (authoritative); nif inline texture set fallback.
        tex = self._material_textures(shape) or shape.textures
        diffuse = tex.get("Diffuse")
        if not diffuse:
            return None
        return {
            "diffuse": _norm_tex(diffuse),
            "normal": _norm_tex(tex["Normal"]) if tex.get("Normal") else None,
            "specular": _norm_tex(tex["Specular"]) if tex.get("Specular") else None,
        }

    def get(self, race_edid: str, sex: Sex) -> Optional[dict]:
        key = (race_edid, sex)
        if key in self._cache:
            return self._cache[key]
        result = None
        for hp in self.pools.pool(race_edid, sex, "Face"):
            modl = hp.get_subrecord("MODL")
            if modl is None:
                continue
            model_rel = modl.data.rstrip(b"\x00").decode("cp1252", "replace")
            result = self._read_nif_textures(model_rel)
            if result is not None:
                break
        self._cache[key] = result
        return result
