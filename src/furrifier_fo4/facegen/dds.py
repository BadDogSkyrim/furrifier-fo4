"""Minimal DDS writer for BC7 face output.

What we need: read an RGBA image (numpy uint8), produce a BC7_UNORM DDS
with a full mip chain. The FO4 FaceCustomization diffuse is a full head
texture the engine STREAMS and mip-samples (shadow/LOD passes) — a single
level (mipMapCount=1) makes a shader request a mip that doesn't exist and
the GPU access-violates at distance/in shadow (d3d11 crash). (Skyrim's
shipped *face-tint overlays* are mipMapCount=1 because they're always
close-up — a wrong carry-over for FO4's full diffuse.)

DDS file layout for BC7:

    [4 bytes]   magic  = "DDS "
    [124 bytes] DDS_HEADER  (size=124, FourCC="DX10")
    [20 bytes]  DDS_HEADER_DXT10  (DXGI_FORMAT_BC7_UNORM, TEXTURE2D)
    [...]       block data, mip 0, mip 1, ... (each ceil(w/4)*ceil(h/4) blocks)

References: Microsoft's DDS_HEADER / DDS_HEADER_DXT10 docs and the
DirectXTex source. Headers are tiny but every byte matters — the loader
is unforgiving about malformed dwSize / pitch / mip-count fields.
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


def _build_dds_header(width: int, height: int, block_payload_bytes: int,
                      mip_count: int) -> bytes:
    pixelformat = struct.pack(
        "<I I 4s I I I I I",
        32,                      # dwSize
        _DDPF_FOURCC,            # dwFlags (FourCC tells loader to read DXT10 ext)
        b"DX10",                 # dwFourCC
        0, 0, 0, 0, 0)           # bit masks (unused with FourCC)

    flags = (_DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH
             | _DDSD_PIXELFORMAT | _DDSD_LINEARSIZE)
    caps1 = _DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= _DDSD_MIPMAPCOUNT
        caps1 |= _DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP

    header = struct.pack(
        "<I I I I I I I 11I 32s 5I",
        124,                              # dwSize
        flags,                            # dwFlags
        height, width,                    # dwHeight, dwWidth
        block_payload_bytes,              # dwPitchOrLinearSize (mip 0 size)
        0,                                # dwDepth
        mip_count,                        # dwMipMapCount
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


def _downsample(rgba: np.ndarray) -> np.ndarray:
    """Box-filter 2x downsample (next mip). Even dims (power-of-2 chain);
    an odd trailing row/col is dropped."""
    h, w = (rgba.shape[0] & ~1) or 1, (rgba.shape[1] & ~1) or 1
    r = rgba[:h, :w].reshape(h // 2, 2, w // 2, 2, 4).astype(np.uint16)
    return r.mean(axis=(1, 3)).round().astype(np.uint8)


def _mip_levels(rgba: np.ndarray) -> list:
    """Full mip pyramid from `rgba` down to 1x1."""
    levels = [rgba]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        levels.append(_downsample(levels[-1]))
    return levels


def _encode_level(rgba: np.ndarray, uber_level: int, perceptual: bool) -> bytes:
    """BC7-encode one mip level. Levels below 4x4 are edge-padded to a single
    4x4 block (the DDS stores one block for sub-4 mips)."""
    h, w = rgba.shape[:2]
    pw, ph = (w + 3) & ~3, (h + 3) & ~3
    if (pw, ph) != (w, h):
        pad = np.empty((ph, pw, 4), np.uint8)
        pad[:h, :w] = rgba
        pad[:h, w:] = rgba[:, w - 1:w]      # replicate last column
        pad[h:, :] = pad[h - 1:h, :]        # replicate last row
        rgba = pad
    return bc7.encode_image(rgba, uber_level=uber_level, perceptual=perceptual)


def write_bc7_dds(path: Path, rgba: np.ndarray, *,
                  uber_level: int = 0,
                  perceptual: bool = True,
                  mips: bool = True) -> Path:
    """Encode ``rgba`` (numpy uint8, ``[H, W, 4]``) to a BC7_UNORM DDS with a
    full mip chain (``mips=False`` for a single level). Returns ``path``."""
    if rgba.dtype != np.uint8:
        raise TypeError(f"rgba must be uint8, got {rgba.dtype}")
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"rgba must be (H, W, 4), got {rgba.shape}")
    h, w = rgba.shape[:2]
    if (w & 3) or (h & 3):
        raise ValueError(
            f"width and height must be multiples of 4, got {w}x{h}")

    levels = _mip_levels(rgba) if mips else [rgba]
    payload = b"".join(
        _encode_level(lv, uber_level, perceptual) for lv in levels)
    mip0_bytes = (w >> 2) * (h >> 2) * 16
    header = _build_dds_header(w, h, mip0_bytes, len(levels))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.write(payload)
    return path
