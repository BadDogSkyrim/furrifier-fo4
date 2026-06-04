"""ctypes binding to bc7enc_wrapper.dll.

The DLL is a thin shim around Rich Geldreich's `bc7enc` (vendored
under ``furrifier/native/bc7enc/``). It exposes one entry point that
takes a whole RGBA image and produces a packed BC7 block stream — the
per-block FFI overhead would otherwise dominate, since a 2048² face
tint is 32k blocks.

The DLL is built once via ``furrifier/native/build_bc7enc.cmd`` and
shipped inside the package as ``_bc7enc.dll`` so PyInstaller bundles
it automatically.
"""
from __future__ import annotations

import ctypes
import threading
from pathlib import Path

import numpy as np


# Search order: alongside this module first (dev + frozen), then the
# native build dir as a fallback for ``python -m`` style invocations
# from a fresh checkout.
_HERE = Path(__file__).parent
_DLL_NAME = "_bc7enc.dll"
_CANDIDATES = [
    _HERE / _DLL_NAME,
    _HERE.parents[2] / "native" / "bc7enc" / "bc7enc_wrapper.dll",
]


def _load_dll() -> ctypes.CDLL:
    for path in _CANDIDATES:
        if path.is_file():
            return ctypes.CDLL(str(path))
    raise FileNotFoundError(
        f"bc7enc DLL not found. Tried: {[str(p) for p in _CANDIDATES]}. "
        f"Run `furrifier/native/build_bc7enc.cmd` to rebuild it.")


_lib = _load_dll()
_lib.bc7enc_compress_image_rgba.argtypes = [
    ctypes.c_char_p,            # rgba bytes
    ctypes.c_int, ctypes.c_int, # width, height
    ctypes.c_char_p,            # out blocks
    ctypes.c_int,               # uber_level
    ctypes.c_int,               # max_partitions_mode
    ctypes.c_int,               # perceptual flag
]
_lib.bc7enc_compress_image_rgba.restype = ctypes.c_int


# bc7enc's internal init has a global table; harmless to re-init but
# we cache to avoid the (cheap) cost when running many encodes in a
# threadpool. The DLL itself idempotents the init call.
_init_lock = threading.Lock()


def encode_image(rgba: np.ndarray, *,
                 uber_level: int = 0,
                 max_partitions: int = 64,
                 perceptual: bool = True) -> bytes:
    """Compress an RGBA image (numpy uint8, shape ``[H, W, 4]``) to a
    packed BC7 block stream — no DDS header, just the blocks.

    `uber_level` is bc7enc's quality dial, 0..4. 0 is fast and already
    matches or beats texconv's default quality on face tints (RMS ~0.21
    vs 0.25 in our 2026-04-25 bake-off); higher levels are dramatically
    slower for marginal RMS improvements. Stick with 0 unless someone
    has a specific reason.

    `perceptual` controls whether bc7enc minimises error in YCbCr space
    (True) or RGB space (False). Default True.

    Width and height must each be a multiple of 4 — the BC7 spec is
    block-based and this function does not pad. For mip levels below
    4×4, encode in something else or skip.
    """
    if rgba.dtype != np.uint8:
        raise TypeError(f"rgba must be uint8, got {rgba.dtype}")
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"rgba must be (H, W, 4), got {rgba.shape}")
    h, w = rgba.shape[:2]
    if (w & 3) or (h & 3):
        raise ValueError(
            f"width and height must be multiples of 4, got {w}x{h}")

    # `tobytes()` is cheap if the array is already contiguous; numpy
    # makes one when needed. ctypes.c_char_p needs a contiguous buffer.
    if not rgba.flags["C_CONTIGUOUS"]:
        rgba = np.ascontiguousarray(rgba)
    rgba_bytes = rgba.tobytes()

    n_blocks = (w >> 2) * (h >> 2)
    out = ctypes.create_string_buffer(n_blocks * 16)

    rc = _lib.bc7enc_compress_image_rgba(
        rgba_bytes, w, h, out,
        int(uber_level), int(max_partitions), 1 if perceptual else 0)
    if rc != 0:
        raise RuntimeError(f"bc7enc_compress_image_rgba returned {rc}")
    return bytes(out.raw)
