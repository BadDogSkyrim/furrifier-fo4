"""Qt Quick 3D scene embedded in a QWidget (FO4).

Loads an assembled facegeom nif and renders its shapes via Qt Quick 3D.
Unlike the Skyrim preview, the head diffuse is already the baked
FaceCustomization texture (tints composited in at bake time), so there's no
in-preview compositing — every shape just uses its own Diffuse slot. The
head's FaceCustomization texture lives in the per-bake temp tree; everything
else (eyes, hair, neck) resolves from the game Data via AssetResolver.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

from .._pyn import ensure_dev_path
ensure_dev_path()

from PySide6.QtCore import QByteArray, QObject, QUrl, Property
from PySide6.QtGui import QVector3D
from PySide6.QtQuick3D import QQuick3DGeometry
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from ..facegen.assets import AssetResolver

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    QML_FILE = Path(sys._MEIPASS) / "furrifier_fo4" / "preview" / "scene.qml"  # type: ignore[attr-defined]
else:
    QML_FILE = Path(__file__).parent / "scene.qml"


# ---- geometry --------------------------------------------------------------


class FacegenShapeGeometry(QQuick3DGeometry):
    """One facegen shape as a Qt Quick 3D geometry buffer:
    interleaved POSITION(3f)+NORMAL(3f)+UV(2f), uint32 index list."""

    STRIDE = (3 + 3 + 2) * 4

    def populate_from(self, shape: dict) -> None:
        verts = shape["verts"]
        tris = shape["tris"]
        n = len(verts)
        uvs = (shape["uvs"] if shape["uvs"] is not None
               else np.zeros((n, 2), dtype=np.float32))
        normals = shape["normals"]
        if normals is None:
            normals = _face_normals(verts, tris)

        interleaved = np.hstack([verts, normals, uvs]).astype(np.float32)
        self.clear()
        self.setStride(self.STRIDE)
        self.setVertexData(QByteArray(interleaved.tobytes()))
        self.setIndexData(QByteArray(tris.astype(np.uint32).tobytes()))
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        A = QQuick3DGeometry.Attribute
        self.addAttribute(A.Semantic.PositionSemantic, 0,
                          A.ComponentType.F32Type)
        self.addAttribute(A.Semantic.NormalSemantic, 12,
                          A.ComponentType.F32Type)
        self.addAttribute(A.Semantic.TexCoord0Semantic, 24,
                          A.ComponentType.F32Type)
        self.addAttribute(A.Semantic.IndexSemantic, 0,
                          A.ComponentType.U32Type)
        bmin = verts.min(axis=0)
        bmax = verts.max(axis=0)
        self.setBounds(QVector3D(*(float(c) for c in bmin)),
                       QVector3D(*(float(c) for c in bmax)))
        self.update()


def _face_normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    out = np.zeros_like(verts)
    np.add.at(out, tris[:, 0], fn)
    np.add.at(out, tris[:, 1], fn)
    np.add.at(out, tris[:, 2], fn)
    lengths = np.linalg.norm(out, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return (out / lengths).astype(np.float32)


# ---- shape loading ---------------------------------------------------------


def _rigid_preview_xform(shape, nif):
    """Approximate linear-blend skinning with the dominant bone's rigid
    transform so skinned shapes (eyes, hair) land in the head's frame.
    The facebone skin-to-bone rotations matter here — they're what keeps
    eyes/hair aligned instead of warped. None for non-skinned shapes."""
    try:
        bone_weights = shape.bone_weights
    except Exception:
        return None
    if not bone_weights:
        return None
    dominant = max(bone_weights,
                   key=lambda b: sum(w for _, w in bone_weights[b]))
    if dominant not in nif.nodes:
        return None
    bone_xf = nif.nodes[dominant].transform
    s2b = shape.get_shape_skin_to_bone(dominant)
    bone_trans = np.array(list(bone_xf.translation), dtype=np.float32)
    bone_rot = np.array([list(r) for r in bone_xf.rotation], dtype=np.float32)
    s2b_trans = np.array(list(s2b.translation), dtype=np.float32)
    s2b_rot = np.array([list(r) for r in s2b.rotation], dtype=np.float32)
    return bone_rot @ s2b_rot, bone_rot @ s2b_trans + bone_trans


ALPHA_DEFAULT = "Default"
ALPHA_MASK = "Mask"
ALPHA_BLEND = "Blend"


def _alpha_from_nif_shape(shape) -> tuple:
    if not shape.has_alpha_property:
        return (ALPHA_DEFAULT, 0.5)
    props = shape.alpha_property.properties
    if props.alpha_blend:
        return (ALPHA_BLEND, 0.5)
    if props.alpha_test:
        return (ALPHA_MASK, max(0.0, min(1.0, props.threshold / 255.0)))
    return (ALPHA_DEFAULT, 0.5)


def load_nif_shapes(nif_path: Path) -> List[dict]:
    from pyn.pynifly import NifFile

    nif = NifFile(str(nif_path))
    shapes = []
    for shape in nif.shapes:
        verts = np.asarray(shape.verts, dtype=np.float32)
        tris = np.asarray(shape.tris, dtype=np.uint32)
        uvs = np.asarray(shape.uvs, dtype=np.float32) if shape.uvs else None
        if uvs is not None:
            uvs = uvs.copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]  # Qt samples bottom-left origin.
        raw_normals = (np.asarray(shape.normals, dtype=np.float32)
                       if shape.normals else None)
        if raw_normals is not None and not np.any(raw_normals):
            raw_normals = None
        xf = _rigid_preview_xform(shape, nif)
        if xf is not None:
            rot, trans = xf
            verts = verts @ rot.T + trans
            if raw_normals is not None:
                raw_normals = raw_normals @ rot.T
        alpha_mode, alpha_cutoff = _alpha_from_nif_shape(shape)
        # Greyscale-to-palette (FO4 hair): the diffuse is a grayscale map
        # coloured through the Greyscale gradient at grayscaleToPaletteScale.
        greyscale = bool(getattr(shape.shader, "flag_greyscale_color", False))
        palette_scale = 0.0
        greyscale_tex = ""
        if greyscale:
            try:
                palette_scale = float(
                    shape.shader.properties.grayscaleToPaletteScale)
            except Exception:
                palette_scale = 0.0
            greyscale_tex = shape.textures.get("Greyscale", "")
        shapes.append({
            "name": shape.name,
            "verts": verts,
            "tris": tris,
            "uvs": uvs,
            "normals": raw_normals,
            "diffuse": shape.textures.get("Diffuse", ""),
            "greyscale": greyscale,
            "greyscale_tex": greyscale_tex,
            "palette_scale": palette_scale,
            "alpha_mode": alpha_mode,
            "alpha_cutoff": alpha_cutoff,
        })
    return shapes


def _resolve_texture_file(relpath: str, resolver: AssetResolver,
                          bake_root: Optional[Path] = None) -> Optional[Path]:
    """Resolve a `textures\\…` relpath to a file on disk. `bake_root` (the
    per-bake temp tree, where the head's FaceCustomization lives) wins, then
    the game Data resolver (loose + BA2)."""
    if not relpath:
        return None
    rel = relpath.lstrip("\\/").replace("\\", "/")
    if not rel.lower().startswith("textures/"):
        rel = "textures/" + rel
    if bake_root is not None:
        cand = bake_root / rel
        if cand.is_file():
            return cand
    return resolver.resolve(rel)


def _mtime_token(path) -> str:
    """A short cache-busting token from a file's mtime (falls back to 0)."""
    try:
        return str(Path(path).stat().st_mtime_ns)
    except OSError:
        return "0"


def resolve_and_convert_diffuse(relpath: str, resolver: AssetResolver,
                                temp_dir: Path,
                                bake_root: Optional[Path] = None) -> Optional[str]:
    """Resolve a `textures\\…` relpath, decode DDS → PNG, return a file:// URL."""
    src = _resolve_texture_file(relpath, resolver, bake_root)
    if src is None:
        log.warning("texture missing: %s", relpath)
        return None
    # Content-address the PNG by the source's mtime, so a re-baked DDS (e.g. a
    # different variant written to the same formid path on a Roll step) gets a
    # FRESH PNG + URL instead of the stale cached one — while an unchanged
    # texture (navigating back) is still reused.
    png_path = temp_dir / f"{Path(src).stem}_{_mtime_token(src)}.png"
    if not png_path.exists():
        try:
            Image.open(src).convert("RGBA").save(png_path)
        except Exception as exc:
            log.warning("texture decode failed %s: %s", relpath, exc)
            return None
    return QUrl.fromLocalFile(str(png_path)).toString()


def resolve_greyscale_hair(diffuse_rel: str, greyscale_rel: str,
                           palette_scale: float, resolver: AssetResolver,
                           temp_dir: Path,
                           bake_root: Optional[Path] = None) -> Optional[str]:
    """Composite an FO4 greyscale-to-palette hair diffuse into a coloured PNG.

    The grayscale diffuse luminance is the U lookup into the hair-color
    gradient; `palette_scale` (the NPC's hair colour, 0..1) is the V row. The
    diffuse alpha (hair-card cutout) is preserved. Falls back to the plain
    diffuse if the gradient can't be resolved/decoded."""
    diff_src = _resolve_texture_file(diffuse_rel, resolver, bake_root)
    grad_src = _resolve_texture_file(greyscale_rel, resolver, bake_root)
    if diff_src is None:
        log.warning("hair diffuse missing: %s", diffuse_rel)
        return None
    if grad_src is None:
        log.warning("hair gradient missing: %s — showing raw grayscale",
                    greyscale_rel)
        return resolve_and_convert_diffuse(diffuse_rel, resolver, temp_dir,
                                           bake_root)
    out_path = temp_dir / (
        f"{Path(diff_src).stem}_{_mtime_token(diff_src)}"
        f"_hair{int(round(palette_scale * 1000)):04d}.png")
    if out_path.exists():
        return QUrl.fromLocalFile(str(out_path)).toString()
    try:
        diff = np.asarray(Image.open(diff_src).convert("RGBA"))
        grad = np.asarray(Image.open(grad_src).convert("RGB"))
    except Exception as exc:
        log.warning("hair composite decode failed (%s / %s): %s",
                    diffuse_rel, greyscale_rel, exc)
        return resolve_and_convert_diffuse(diffuse_rel, resolver, temp_dir,
                                           bake_root)
    gh, gw = grad.shape[:2]
    lum = diff[:, :, 0].astype(np.float32) / 255.0          # grayscale: R==G==B
    u = np.clip(np.rint(lum * (gw - 1)).astype(np.intp), 0, gw - 1)
    v = int(min(max(round(palette_scale * (gh - 1)), 0), gh - 1))
    out = diff.copy()
    out[:, :, :3] = grad[v, u]                              # LUT lookup per pixel
    Image.fromarray(out, "RGBA").save(out_path)
    return QUrl.fromLocalFile(str(out_path)).toString()


# ---- QML-side models -------------------------------------------------------


class ShapeModel(QObject):
    def __init__(self, name: str, geometry: FacegenShapeGeometry,
                 diffuse_url: str, alpha_mode: str = ALPHA_DEFAULT,
                 alpha_cutoff: float = 0.5,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._name = name
        self._geometry = geometry
        self._diffuse_url = diffuse_url
        self._alpha_mode = alpha_mode
        self._alpha_cutoff = float(alpha_cutoff)

    @Property(str, constant=True)
    def name(self) -> str:
        return self._name

    @Property(QObject, constant=True)
    def geometry(self):
        return self._geometry

    @Property(str, constant=True)
    def diffuseUrl(self) -> str:
        return self._diffuse_url

    @Property(str, constant=True)
    def alphaMode(self) -> str:
        return self._alpha_mode

    @Property(float, constant=True)
    def alphaCutoff(self) -> float:
        return self._alpha_cutoff

    @Property(str, constant=True)
    def baseColor(self) -> str:
        # FO4 tints are baked into the diffuse; no shader-side tint.
        return "#ffffff"


class PreviewContext(QObject):
    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shapes: list = []
        self._center = QVector3D(0.0, 0.0, 0.0)
        self._radius = 50.0

    @Property(list, constant=True)
    def shapes(self) -> list:
        return self._shapes

    @Property(QVector3D, constant=True)
    def center(self) -> QVector3D:
        return self._center

    @Property(float, constant=True)
    def radius(self) -> float:
        return self._radius

    def set_scene(self, shapes: list, center: QVector3D, radius: float) -> None:
        for s in shapes:
            s.setParent(self)
        self._shapes = shapes
        self._center = center
        self._radius = max(radius, 1.0)


# ---- the widget ------------------------------------------------------------


class FacegenSceneWidget(QWidget):
    """Renders one facegeom nif via Qt Quick 3D. Call `set_nif`."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._temp_dir = Path(tempfile.mkdtemp(prefix="fo4_preview_tex_"))
        self._resolver: Optional[AssetResolver] = None
        self._resolver_data_dir: Optional[Path] = None
        self._ctx = PreviewContext(self)
        self._quick = QQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._quick.rootContext().setContextProperty("previewCtx", self._ctx)
        self._quick.setSource(QUrl.fromLocalFile(str(QML_FILE)))
        self._quick.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        ow, oh = self.width(), self.height()
        if ow <= 0 or oh <= 0:
            return
        if ow * 4 <= oh * 3:
            nw, nh = ow, (ow * 4) // 3
        else:
            nh, nw = oh, (oh * 3) // 4
        self._quick.setGeometry((ow - nw) // 2, (oh - nh) // 2, nw, nh)

    def clear(self) -> None:
        self._ctx.set_scene([], QVector3D(0, 0, 0), 50.0)

    def _ensure_resolver(self, data_dir: Path) -> AssetResolver:
        data_dir = Path(data_dir)
        if self._resolver is None or self._resolver_data_dir != data_dir:
            if self._resolver is not None:
                self._resolver.close()
            self._resolver = AssetResolver.for_data_dir(data_dir)
            self._resolver_data_dir = data_dir
        return self._resolver

    def set_nif(self, nif_path: Path, data_dir: Path,
                bake_root: Optional[Path] = None,
                preserve_camera: bool = False) -> None:
        nif_path = Path(nif_path)
        resolver = self._ensure_resolver(data_dir)
        shapes_raw = load_nif_shapes(nif_path)
        shape_models = []
        all_verts = []
        for s in shapes_raw:
            if len(s["verts"]) == 0 or len(s["tris"]) == 0:
                continue
            geom = FacegenShapeGeometry()
            geom.populate_from(s)
            if s.get("greyscale") and s.get("greyscale_tex"):
                diffuse_url = resolve_greyscale_hair(
                    s["diffuse"], s["greyscale_tex"], s["palette_scale"],
                    resolver, self._temp_dir, bake_root) or ""
            else:
                diffuse_url = resolve_and_convert_diffuse(
                    s["diffuse"], resolver, self._temp_dir, bake_root) or ""
            shape_models.append(ShapeModel(
                s["name"], geom, diffuse_url,
                alpha_mode=s.get("alpha_mode", ALPHA_DEFAULT),
                alpha_cutoff=s.get("alpha_cutoff", 0.5)))
            all_verts.append(s["verts"])

        if all_verts:
            combined = np.concatenate(all_verts, axis=0)
            c = combined.mean(axis=0)
            radius = float(np.linalg.norm(combined - c, axis=1).max())
            # Skyrim/FO4 (x,y,z) → Qt scene (-x, z, y).
            center = QVector3D(-float(c[0]), float(c[2]), float(c[1]))
        else:
            log.warning("%s: no shapes loaded", nif_path.name)
            center, radius = QVector3D(0, 0, 0), 50.0

        saved_cam = None
        if preserve_camera:
            root = self._quick.rootObject()
            if root is not None:
                saved_cam = tuple(root.property(p) for p in
                                  ("yaw", "pitch", "distance", "panX", "panY"))

        self._ctx.set_scene(shape_models, center, radius)
        self._quick.rootContext().setContextProperty("previewCtx", self._ctx)

        if saved_cam is not None:
            root = self._quick.rootObject()
            if root is not None:
                for name, val in zip(
                        ("yaw", "pitch", "distance", "panX", "panY"), saved_cam):
                    root.setProperty(name, val)

    def reframe_camera(self) -> None:
        root = self._quick.rootObject()
        if root is None:
            return
        radius = root.property("radius") or self._ctx.radius
        for name, val in (("yaw", 0.0), ("pitch", 0.0), ("panX", 0.0),
                          ("panY", 0.0), ("distance", float(radius) * 2.5)):
            root.setProperty(name, val)

    def closeEvent(self, event) -> None:
        if self._resolver is not None:
            self._resolver.close()
            self._resolver = None
        import shutil
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        super().closeEvent(event)
