"""Texture loading. Every failure names the file that failed.

Two jobs, both learned from a run that died with a bare
``NotImplementedError: Unimplemented DXGI format 87`` and no hint as to
which of the several hundred textures in the load order produced it:

1. Wrap the load so any failure — missing, corrupt, or a format nobody
   can decode — raises `TextureLoadError` with the path in the message.
2. Decode the uncompressed BGRA DDS formats Pillow's `DdsImagePlugin`
   rejects. It covers the block-compressed families and DX10
   R8G8B8A8, but a DX10 header carrying dxgiFormat 87
   (B8G8R8A8_UNORM) — plain 32-bit BGRA, no block compression —
   falls through to "Unimplemented DXGI format". There is nothing to
   decompress, only a channel order Pillow's raw decoder already knows.

DDS *writing* lives in `dds.py`. This module stays clear of it so the
read path doesn't drag in the bc7 encoder DLL.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional, Union

from PIL import Image


# DX10 dxgiFormat -> (PIL mode, raw-decoder mode). The uncompressed 32-bit
# BGRA family; Pillow decodes the RGBA-ordered ones (28/29/30) itself.
_DXGI_RAWMODE = {
    87: ("RGBA", "BGRA"),   # B8G8R8A8_UNORM
    88: ("RGB", "BGRX"),    # B8G8R8X8_UNORM  (X = padding, no alpha)
    91: ("RGBA", "BGRA"),   # B8G8R8A8_UNORM_SRGB
    93: ("RGB", "BGRX"),    # B8G8R8X8_UNORM_SRGB
}

_MAGIC = b"DDS "
_HEADER_END = 128           # magic (4) + DDS_HEADER (124)
_DXT10_SIZE = 20
_OFF_HEIGHT = 12            # DDS_HEADER fields, offsets from file start
_OFF_FOURCC = 84


class TextureLoadError(Exception):
    """A texture could not be opened or decoded. Always names the path."""


def _read_dx10_uncompressed(path: Path) -> Optional[Image.Image]:
    """Decode mip 0 of an uncompressed DX10 DDS.

    Returns None if `path` isn't one of the formats this fallback covers,
    so the caller can report Pillow's original complaint instead.
    """
    with open(path, "rb") as f:
        head = f.read(_HEADER_END + _DXT10_SIZE)
        if len(head) < _HEADER_END + _DXT10_SIZE:
            return None
        if head[:4] != _MAGIC or head[_OFF_FOURCC:_OFF_FOURCC + 4] != b"DX10":
            return None
        height, width = struct.unpack_from("<2I", head, _OFF_HEIGHT)
        (dxgi_format,) = struct.unpack_from("<I", head, _HEADER_END)
        entry = _DXGI_RAWMODE.get(dxgi_format)
        if entry is None or not width or not height:
            return None
        mode, rawmode = entry
        stride = width * 4
        data = f.read(stride * height)

    if len(data) < stride * height:
        raise TextureLoadError(
            f"{path}: truncated DDS -- {width}x{height} needs "
            f"{stride * height} bytes of pixel data, file has {len(data)}")
    return Image.frombuffer(mode, (width, height), data,
                            "raw", rawmode, stride, 1)


def open_texture(path: Union[str, Path]) -> Image.Image:
    """Open a texture and parse its header, without decoding the pixels.

    Use this only to probe size/mode -- Pillow is lazy, so skipping the
    decode is near-free. Anything that touches pixels should go through
    `load_texture`, which forces decode errors to surface here, where the
    path is still in hand.
    """
    path = Path(path)
    try:
        return Image.open(path)
    except Exception as exc:
        image = _read_dx10_uncompressed(path) if path.is_file() else None
        if image is not None:
            return image
        raise TextureLoadError(f"{path}: {exc}") from exc


def load_texture(path: Union[str, Path],
                 mode: Optional[str] = None) -> Image.Image:
    """Open and fully decode a texture, converting to `mode` if given."""
    path = Path(path)
    image = open_texture(path)
    try:
        if mode is not None:
            return image.convert(mode)
        image.load()
        return image
    except Exception as exc:
        raise TextureLoadError(f"{path}: {exc}") from exc
