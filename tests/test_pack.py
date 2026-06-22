"""Pack loose facegen into BA2 archives — synthetic tree, no game files."""

import struct

from esplib import Ba2Reader

from furrifier_fo4.pack import pack_facegen


def _dx10_dds(width=8, height=8, num_mips=1, fmt=98, body=None):
    """A minimal DX10 (BC7) .dds: magic + DDS_HEADER + DXT10 header + body."""
    if body is None:
        body = bytes((i * 7) % 256 for i in range(64))  # 8x8 BC7 = 4 blocks
    pixelformat = struct.pack('<I I 4s I I I I I', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    header = struct.pack('<7I 44s 32s 5I', 124, 0x1007, height, width,
                         len(body), 0, num_mips, b'\x00' * 44, pixelformat,
                         0x1000, 0, 0, 0, 0)
    dxt10 = struct.pack('<5I', fmt, 3, 0, 1, 0)
    return b'DDS ' + header + dxt10 + body


def _make_tree(root):
    """A tiny facegen output tree: two nifs + two diffuse dds across two
    base-master subfolders (mirrors the real layout)."""
    geom = root / "meshes/Actors/Character/FaceGenData/FaceGeom"
    cust = root / "textures/Actors/Character/FaceCustomization"
    files = {}
    for plugin, fid in (("Fallout4.esm", "00012345"), ("DLCCoast.esm", "01000abc")):
        nif = geom / plugin / f"{fid}.nif"
        nif.parent.mkdir(parents=True, exist_ok=True)
        nif.write_bytes(b"NIF-" + fid.encode())
        files[nif] = b"NIF-" + fid.encode()
        dds = cust / plugin / f"{fid}_d.dds"
        dds.parent.mkdir(parents=True, exist_ok=True)
        dds_bytes = _dx10_dds(body=bytes((int(fid, 16) + i) % 256 for i in range(64)))
        dds.write_bytes(dds_bytes)
    return geom, cust


def test_pack_creates_both_archives_and_removes_loose(tmp_path):
    geom, cust = _make_tree(tmp_path)
    written = pack_facegen(tmp_path, "FO4FurryPatch.esp")

    names = sorted(p.name for p in written)
    assert names == ["FO4FurryPatch - Main.ba2", "FO4FurryPatch - Textures.ba2"]
    assert all(p.exists() for p in written)

    # Loose trees removed (loose would otherwise shadow the archive in FO4).
    assert not geom.exists()
    assert not cust.exists()

    main = tmp_path / "FO4FurryPatch - Main.ba2"
    with Ba2Reader(main) as r:
        assert r.archive_type == "GNRL"
        assert r.has_file(
            r"meshes\Actors\Character\FaceGenData\FaceGeom\Fallout4.esm\00012345.nif")
        assert r.read_file(
            r"meshes\Actors\Character\FaceGenData\FaceGeom\DLCCoast.esm\01000abc.nif"
        ) == b"NIF-01000abc"

    tex = tmp_path / "FO4FurryPatch - Textures.ba2"
    with Ba2Reader(tex) as r:
        assert r.archive_type == "DX10"
        assert r.has_file(
            r"textures\Actors\Character\FaceCustomization\Fallout4.esm\00012345_d.dds")


def test_archive_name_tracks_patch_stem(tmp_path):
    _make_tree(tmp_path)
    written = pack_facegen(tmp_path, "MyFurry.esp")
    assert sorted(p.name for p in written) == [
        "MyFurry - Main.ba2", "MyFurry - Textures.ba2"]


def test_empty_subtree_skipped(tmp_path):
    # Only meshes present -> only Main.ba2, textures tree absent so skipped.
    geom = tmp_path / "meshes/Actors/Character/FaceGenData/FaceGeom/Fallout4.esm"
    geom.mkdir(parents=True)
    (geom / "00012345.nif").write_bytes(b"NIF")
    written = pack_facegen(tmp_path, "FO4FurryPatch.esp")
    assert [p.name for p in written] == ["FO4FurryPatch - Main.ba2"]


def test_no_facegen_writes_nothing(tmp_path):
    written = pack_facegen(tmp_path, "FO4FurryPatch.esp")
    assert written == []
    assert not (tmp_path / "FO4FurryPatch - Main.ba2").exists()
