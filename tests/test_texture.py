"""Texture loading: the uncompressed-BGRA DDS fallback, and path-in-error.

Every failure must name the file. A run that dies with a bare
"Unimplemented DXGI format 87" and no path is unactionable when the load
order holds hundreds of textures.
"""

import struct

import numpy as np
import pytest
from PIL import Image

from furrifier_fo4.facegen.texture import (
    TextureLoadError, load_texture, open_texture,
)


DXGI_B8G8R8A8_UNORM = 87
DXGI_B8G8R8X8_UNORM = 88


def _dx10_dds(path, width, height, dxgi_format, payload):
    """Write a minimal DX10-header DDS. `payload` is mip 0, raw."""
    pixelformat = struct.pack("<I I 4s I I I I I",
                              32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    header = struct.pack("<I I I I I I I 11I 32s 5I",
                         124,                 # dwSize
                         0x1 | 0x2 | 0x4 | 0x1000 | 0x8,  # caps/h/w/pf/pitch
                         height, width,
                         width * 4,           # dwPitchOrLinearSize
                         0, 1,                # dwDepth, dwMipMapCount
                         *([0] * 11),
                         pixelformat,
                         0x1000, 0, 0, 0, 0)
    dxt10 = struct.pack("<I I I I I", dxgi_format, 3, 0, 1, 0)
    path.write_bytes(b"DDS " + header + dxt10 + payload)
    return path


def _bgra_payload(pixels):
    """`pixels` is a list of (r, g, b, a) rows-major; store them B,G,R,A."""
    return bytes(b for (r, g, b, a) in pixels for b in (b, g, r, a))


def test_bgra_dds_decodes_with_channels_in_order(tmp_path):
    # A red, a green, a blue and a half-transparent white pixel in a 2x2.
    pixels = [(255, 0, 0, 255), (0, 255, 0, 255),
              (0, 0, 255, 255), (255, 255, 255, 128)]
    p = _dx10_dds(tmp_path / "bgra.dds", 2, 2, DXGI_B8G8R8A8_UNORM,
                  _bgra_payload(pixels))

    im = load_texture(p)

    assert im.mode == "RGBA"
    assert im.size == (2, 2)
    assert np.asarray(im).reshape(4, 4).tolist() == [list(p) for p in pixels]


def test_bgrx_dds_decodes_as_rgb(tmp_path):
    pixels = [(10, 20, 30, 0), (40, 50, 60, 0)]
    p = _dx10_dds(tmp_path / "bgrx.dds", 2, 1, DXGI_B8G8R8X8_UNORM,
                  _bgra_payload(pixels))

    im = load_texture(p)

    assert im.mode == "RGB"
    assert np.asarray(im).tolist() == [[[10, 20, 30], [40, 50, 60]]]


def test_bgra_dds_converts_and_survives_numpy(tmp_path):
    """The real call shape in the compositor: load, convert, to array."""
    p = _dx10_dds(tmp_path / "bgra.dds", 2, 1, DXGI_B8G8R8A8_UNORM,
                  _bgra_payload([(1, 2, 3, 255), (4, 5, 6, 255)]))

    arr = np.asarray(load_texture(p, "RGB"))

    assert arr.shape == (1, 2, 3)
    assert arr.tolist() == [[[1, 2, 3], [4, 5, 6]]]


def test_open_texture_reports_size_without_decoding(tmp_path):
    p = _dx10_dds(tmp_path / "bgra.dds", 4, 2, DXGI_B8G8R8A8_UNORM,
                  _bgra_payload([(0, 0, 0, 255)] * 8))

    with open_texture(p) as im:
        assert im.size == (4, 2)


def test_png_still_loads(tmp_path):
    p = tmp_path / "plain.png"
    Image.fromarray(np.full((2, 2, 3), 77, dtype=np.uint8), "RGB").save(p)

    assert np.asarray(load_texture(p, "RGB")).tolist() == [[[77] * 3] * 2] * 2


def test_missing_file_names_the_path(tmp_path):
    missing = tmp_path / "nope.dds"

    with pytest.raises(TextureLoadError) as exc:
        load_texture(missing)

    assert str(missing) in str(exc.value)


def test_unsupported_format_names_the_path(tmp_path):
    """A DX10 format the fallback doesn't cover still reports WHICH file."""
    p = _dx10_dds(tmp_path / "weird.dds", 4, 4, 999, b"\0" * 16)

    with pytest.raises(TextureLoadError) as exc:
        load_texture(p)

    assert str(p) in str(exc.value)
    assert "999" in str(exc.value)


def test_truncated_payload_names_the_path(tmp_path):
    p = _dx10_dds(tmp_path / "short.dds", 4, 4, DXGI_B8G8R8A8_UNORM,
                  b"\0" * 16)  # needs 64 bytes

    with pytest.raises(TextureLoadError) as exc:
        load_texture(p)

    assert str(p) in str(exc.value)


def test_garbage_file_names_the_path(tmp_path):
    p = tmp_path / "garbage.dds"
    p.write_bytes(b"not a texture at all")

    with pytest.raises(TextureLoadError) as exc:
        load_texture(p)

    assert str(p) in str(exc.value)
