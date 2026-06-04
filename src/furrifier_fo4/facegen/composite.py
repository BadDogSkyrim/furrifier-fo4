"""Bake an FO4 NPC's FaceCustomization diffuse — the full, flattened face.

FO4 (unlike Skyrim's FaceTint *overlay*) bakes a complete, opaque per-NPC
face texture set under
`textures\\Actors\\Character\\FaceCustomization\\<plugin>\\<formid>_{d,msn,s}.dds`.
The diffuse `_d` is the race's base head diffuse with every tint layer
composited *in*. Tints only change colour, so the normal (`_msn`) and
specular (`_s`) pass straight through from the base head — we bake only `_d`.

The point of doing this ourselves: FO4 stores a real Blend Operation per
tint layer (Default/Multiply/Overlay/Soft Light/Hard Light), and the CK's
bake flattens every layer with plain alpha-over, discarding it. We honor it.

Compositing is uniform: start from the base head diffuse, then for each tint
layer in record order, blend its colour against the accumulated result via
its blend op and alpha-composite with opacity = mask_coverage * intensity.
The skin-tone layer isn't special — it's just the first layer (a Soft Light
fill over the whole face through its SkinTone mask). Output is opaque RGB.

Missing masks are skipped with a warning; one bad reference must not bail
the whole face.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from . import blend as _blend
from .assets import AssetResolver

log = logging.getLogger(__name__)

VALID_OUTPUT_SIZES = (256, 512, 1024, 2048, 4096)


def _load_rgb(path: Path, target_size: Optional[int] = None) -> np.ndarray:
    """Load an image as RGB float32 in [0,1], optionally resampled square."""
    im = Image.open(path).convert("RGB")
    if target_size is not None and im.size != (target_size, target_size):
        im = im.resize((target_size, target_size), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def _load_coverage(path: Path, size: int) -> np.ndarray:
    """Load a tint mask and return grayscale coverage (2D float32 in [0,1])."""
    im = Image.open(path).convert("RGB")
    if im.size != (size, size):
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    rgb = np.asarray(im, dtype=np.float32) / 255.0
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114)


def composite_diffuse(resolver: AssetResolver, base_diffuse: str,
                      layers: list[dict],
                      output_size: Optional[int] = None) -> np.ndarray:
    """Composite tint `layers` onto the `base_diffuse` head texture.

    `base_diffuse` is a Data-relative path; `layers` come from
    `extract.npc_tint_layers` ({mask, color:[r,g,b], intensity, blend}).
    Returns an opaque RGB float32 array in [0,1] at the base diffuse's native
    size (or `output_size` if given).
    """
    base_path = resolver.resolve(base_diffuse)
    if base_path is None:
        raise FileNotFoundError(f"base head diffuse not found: {base_diffuse}")

    acc = _load_rgb(base_path, target_size=output_size)
    h, w = acc.shape[:2]
    size = w  # masks are square in the head's UV space

    mask_cache: dict = resolver.image_cache

    def load_cached(relpath: str) -> Optional[np.ndarray]:
        key = (relpath.replace("/", "\\").lower(), size)
        cached = mask_cache.get(key)
        if cached is not None:
            return cached
        p = resolver.resolve(relpath)
        if p is None:
            log.warning("tint mask not found, skipping: %s", relpath)
            return None
        cov = _load_coverage(p, size)
        mask_cache[key] = cov
        return cov

    for layer in layers:
        cov = load_cached(layer["mask"])
        if cov is None:
            continue
        color = np.asarray(layer["color"][:3], dtype=np.float32) / 255.0
        contrib_a = cov * float(layer["intensity"])
        src = _blend.blend(layer.get("blend", 0), acc, color)
        a = contrib_a[..., None]
        acc += a * (src - acc)

    return np.clip(acc, 0.0, 1.0)


def _to_uint8_rgba(rgb: np.ndarray) -> np.ndarray:
    """Opaque RGBA uint8 from an RGB float array (FaceCustomization is opaque)."""
    h, w = rgb.shape[:2]
    out = np.empty((h, w, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def build_facecustomization_png(form_id: str, base_diffuse: str,
                                layers: list[dict], resolver: AssetResolver,
                                out_dir: Path,
                                output_size: Optional[int] = None) -> Path:
    """Composite and save `<formid>_d.png` (debug / visual review)."""
    rgb = composite_diffuse(resolver, base_diffuse, layers, output_size)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{form_id}_d.png"
    Image.fromarray(_to_uint8_rgba(rgb), "RGBA").save(png_path)
    return png_path


def build_facecustomization_dds(form_id: str, base_diffuse: str,
                                layers: list[dict], resolver: AssetResolver,
                                out_dir: Path,
                                output_size: Optional[int] = None) -> Path:
    """Composite + BC7-encode `<formid>_d.dds` (the FaceCustomization diffuse)."""
    from .dds import write_bc7_dds

    if output_size is not None and output_size not in VALID_OUTPUT_SIZES:
        raise ValueError(f"output_size {output_size} not in {VALID_OUTPUT_SIZES}")
    rgb = composite_diffuse(resolver, base_diffuse, layers, output_size)
    out_dir.mkdir(parents=True, exist_ok=True)
    dds_path = out_dir / f"{form_id}_d.dds"
    write_bc7_dds(dds_path, _to_uint8_rgba(rgb))
    return dds_path
