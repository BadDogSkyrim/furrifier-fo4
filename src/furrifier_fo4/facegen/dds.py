"""Minimal DDS writer for BC7 face-tint output.

What we need: read an RGBA image (numpy uint8), produce a Skyrim-
loadable BC7_UNORM DDS. No mip chain — Skyrim's shipped vanilla
face tints have mipMapCount=1 (single level only), since face tints
are always rendered close-up. Skipping mips saves ~25% on file size.

DDS file layout for BC7:

    [4 bytes]   magic  = "DDS "
    [124 bytes] DDS_HEADER  (size=124, FourCC="DX10")
    [20 bytes]  DDS_HEADER_DXT10  (DXGI_FORMAT_BC7_UNORM, TEXTURE2D)
    [...]       block data, single mip level

References: Microsoft's DDS_HEADER / DDS_HEADER_DXT10 docs and the
DirectXTex source. Headers are tiny but every byte matters — Skyrim's
loader is unforgiving about malformed dwSize / pitch fields.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from . import bc7


# DDS header flags (spec values; see d3d9types.h / dds.h).
_DDS_MAGIC = b"DDS "
_DDSD_CAPS = 0x1
_DDSD_HEIGHT = 0x2
_DDSD_WIDTH = 0x4
_DDSD_PIXELFORMAT = 0x1000
_DDSD_LINEARSIZE = 0x80000
_DDSD_MIPMAPCOUNT = 0x20000
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_COMPLEX = 0x8
_DDSCAPS_MIPMAP = 0x400000
_DDPF_FOURCC = 0x4
_DXGI_FORMAT_BC7_UNORM = 98
_D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3


def _build_dds_header(width: int, height: int,
                      block_payload_bytes: int) -> bytes:
    pixelformat = struct.pack(
        "<I I 4s I I I I I",
        32,                      # dwSize
        _DDPF_FOURCC,            # dwFlags (FourCC tells loader to read DXT10 ext)
        b"DX10",                 # dwFourCC
        0, 0, 0, 0, 0)           # bit masks (unused with FourCC)

    flags = (_DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH
             | _DDSD_PIXELFORMAT | _DDSD_LINEARSIZE)
    caps1 = _DDSCAPS_TEXTURE

    header = struct.pack(
        "<I I I I I I I 11I 32s 5I",
        124,                              # dwSize
        flags,                            # dwFlags
        height, width,                    # dwHeight, dwWidth
        block_payload_bytes,              # dwPitchOrLinearSize (mip 0 size)
        0,                                # dwDepth
        1,                                # dwMipMapCount
        *([0] * 11),                      # reserved1
        pixelformat,
        caps1, 0, 0, 0,                   # caps1..4
        0)                                # reserved2

    dxt10 = struct.pack(
        "<I I I I I",
        _DXGI_FORMAT_BC7_UNORM,                       # dxgiFormat
        _D3D10_RESOURCE_DIMENSION_TEXTURE2D,          # resourceDimension
        0,                                            # miscFlag
        1,                                            # arraySize
        0)                                            # miscFlags2

    return _DDS_MAGIC + header + dxt10


def write_bc7_dds(path: Path, rgba: np.ndarray, *,
                  uber_level: int = 0,
                  perceptual: bool = True) -> Path:
    """Encode ``rgba`` (numpy uint8, shape ``[H, W, 4]``) and write the
    result to ``path`` as a single-level BC7_UNORM DDS. Returns ``path``."""
    if rgba.dtype != np.uint8:
        raise TypeError(f"rgba must be uint8, got {rgba.dtype}")
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"rgba must be (H, W, 4), got {rgba.shape}")
    h, w = rgba.shape[:2]
    if (w & 3) or (h & 3):
        raise ValueError(
            f"width and height must be multiples of 4, got {w}x{h}")

    payload = bc7.encode_image(
        rgba, uber_level=uber_level, perceptual=perceptual)
    mip0_bytes = (w >> 2) * (h >> 2) * 16
    header = _build_dds_header(w, h, mip0_bytes)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.write(payload)
    return path
